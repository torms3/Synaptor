import copy
import math
import os
import subprocess

from time import strftime

from cloudvolume import CloudVolume
from cloudvolume.lib import Bbox, Vec, xyzrange, min2, yellow
from taskqueue import GreenTaskQueue

from .synaptortask import SynaptorTask
from synaptor import io


def tup2str(t):
  return " ".join(map(str, t))


def create_init_db_task(storagestr):
    return SynaptorTask(f"init_db {storagestr}")


def create_connected_component_tasks(
    descpath, segpath, storagestr, storagedir,
    cc_thresh, sz_thresh, bounds, shape,
    mip=(8, 8, 40), parallel=1, hashmax=1):

    shape = Vec(*shape)

    vol = CloudVolume(segpath, mip=mip)
    # bounds = vol.bbox_to_mip(bounds, mip=0, to_mip=mip)
    bounds = Bbox.clamp(bounds, vol.bounds)

    class ConnectedComponentsTaskIterator(object):
      def __init__(self, level_start, level_end):
        self.level_start = level_start
        self.level_end = level_end
      def __len__(self):
        return self.level_end - self.level_start
      def __getitem__(self, slc):
        itr = copy.deepcopy(self)
        itr.level_start = self.level_start + slc.start
        itr.level_end = self.level_start + slc.stop
        return itr
      def __iter__(self):
        self.bounds = bounds.clone()
        self.bounds.minpt.z = bounds.minpt.z + self.level_start * shape.z
        self.bounds.maxpt.z = bounds.minpt.z + self.level_end * shape.z

        for startpt in xyzrange( self.bounds.minpt, self.bounds.maxpt, shape ):
          task_shape = min2(shape.clone(), self.bounds.maxpt - startpt)

          task_bounds = Bbox( startpt, startpt + task_shape )
          if task_bounds.volume() < 1:
            continue

          chunk_begin = tup2str(task_bounds.minpt)
          chunk_end = tup2str(task_bounds.maxpt)
          mip_str = tup2str(mip)

          cmd = (f"chunk_ccs {descpath} {segpath} {storagestr}"
                 f" {cc_thresh} {sz_thresh} --chunk_begin {chunk_begin}"
                 f" --chunk_end {chunk_end} --hashmax {hashmax}"
                 f" --parallel {parallel} --mip {mip_str}"
                 f" --storagedir {storagedir}")

          yield SynaptorTask(cmd)

    level_end = int(math.ceil(bounds.size3().z / shape.z))
    return ConnectedComponentsTaskIterator(0, level_end)


def create_merge_ccs_task(
    storagestr, size_thr, max_face_shape, timingtag=None):
    return SynaptorTask(f"merge_ccs {storagestr} {size_thr} "
                        f"--max_face_shape "
                        f"{max_face_shape[0]} {max_face_shape[1]}")


def create_match_contins_tasks(
    storagestr, hashmax, max_faceshape, timingtag=None):

    class MatchContinsTaskIterator(object):
        def __init__(self, hashmax):
            self.level_start = 0
            self.level_end = hashmax

        def __len__(self):
            return self.level_end - self.level_start

        def __getitem__(self, slc):
            itr = copy.deepcopy(self)
            itr.level_start = self.level_start + slc.start
            itr.level_end = self.level_start + slc.stop
            return itr

        def __iter__(self):
            max_faceshape_str = tup2str(max_faceshape)
            for i in range(self.level_start, self.level_end):
                cmd = (f"match_contins {storagestr} {i} "
                       f" --max_face_shape {max_faceshape_str}")

                yield SynaptorTask(cmd)

    return MatchContinsTaskIterator(hashmax)


def create_seg_graph_cc_task(storagestr, hashmax):
    return SynaptorTask(f"seg_graph_ccs {storagestr} {hashmax}")


def create_chunk_seg_map_task(storagestr):
    return SynaptorTask(f"chunk_seg_map {storagestr}")


def create_merge_seginfo_tasks(
    storagestr, hashmax, aux_storagestr=None, 
    szthresh=None, timingtag=None):

    class MergeSeginfoTaskIterator(object):
        def __init__(self, storagestr, hashmax, aux_storagestr, szthresh):
            self.level_start = 0
            self.level_end = hashmax
            self.storagestr = storagestr
            self.aux_storagestr = aux_storagestr
            self.szthresh = szthresh

        def __len__(self):
            return self.level_end - self.level_start

        def __getitem__(self, slc):
            itr = copy.deepcopy(self)
            itr.level_start = self.level_start + slc.start
            itr.level_end = self.level_start + slc.stop
            return itr

        def __iter__(self):
            if self.aux_storagestr is None:
                aux_arg = ""
            else:
                aux_arg = f"--aux_storagestr {self.aux_storagestr}"

            if self.szthresh is not None:
                aux_arg += f" --szthresh {self.szthresh}"

            for i in range(self.level_start, self.level_end):
                cmd = (f"merge_seginfo {self.storagestr} {i} {aux_arg}")

                yield SynaptorTask(cmd)

    return MergeSeginfoTaskIterator(
               storagestr, hashmax, aux_storagestr, szthresh)


def create_chunk_edges_tasks(
    imgpath, cleftpath, segpath, storagestr, hashmax, storagedir,
    bounds, chunkshape, patchsz, resolution=(4, 4, 40)):
    """ Only passing the required arguments for now """
    shape = Vec(*chunkshape)

    class ChunkEdgesTaskIterator(object):
        def __init__(self, level_start, level_end):
            self.level_start = level_start
            self.level_end = level_end

        def __len__(self):
            return self.level_end - self.level_start

        def __getitem__(self, slc):
            itr = copy.deepcopy(self)
            itr.level_start = self.level_start + slc.start
            itr.level_end = self.level_start + slc.stop
            return itr

        def __iter__(self):
            self.bounds = bounds.clone()
            self.bounds.minpt.z = bounds.minpt.z + self.level_start * shape.z
            self.bounds.maxpt.z = bounds.minpt.z + self.level_end * shape.z

            for start in xyzrange(self.bounds.minpt, self.bounds.maxpt, shape):
                task_shape = min2(shape.clone(), self.bounds.maxpt - start)

                task_bounds = Bbox(start, start + task_shape)
                if task_bounds.volume() < 1:
                    continue

                chunk_begin = tup2str(task_bounds.minpt)
                chunk_end = tup2str(task_bounds.maxpt)
                patchsz_str = tup2str(patchsz)
                res_str = tup2str(resolution)

                cmd = (f"chunk_edges {imgpath} {cleftpath} {segpath}"
                       f" {storagestr} {hashmax} --storagedir {storagedir}"
                       f" --chunk_begin {chunk_begin} --chunk_end {chunk_end}"
                       f" --patchsz {patchsz_str} --resolution {res_str}")

                yield SynaptorTask(cmd)

    level_end = int(math.ceil(bounds.size3().z / shape.z))
    return ChunkEdgesTaskIterator(0, level_end)


def create_pick_edge_tasks(storagestr, hashmax):

    class PickEdgeTaskIterator(object):
        def __init__(self, storagestr, hashmax):
            self.level_start = 0
            self.level_end = hashmax
            self.storagestr = storagestr

        def __len__(self):
            return self.level_end - self.level_start

        def __getitem__(self, slc):
            itr = copy.deepcopy(self)
            itr.level_start = self.level_start + slc.start
            itr.level_end = self.level_start + slc.stop
            return itr

        def __iter__(self):
            for i in range(self.level_start, self.level_end):
                cmd = f"pick_edge {self.storagestr} {i}"

                yield SynaptorTask(cmd)

    return PickEdgeTaskIterator(storagestr, hashmax)


def create_merge_dup_tasks(
    storagestr, hashmax, dist_thresh, size_thresh,
    resolution=(4, 4, 40), output_storagestr=None):

    output_storagestr = (storagestr if output_storagestr is None
                         else output_storagestr)

    class MergeDupsTaskIterator(object):
        def __init__(self, storagestr, hashmax):
            self.level_start = 0
            self.level_end = hashmax
            self.storagestr = storagestr

        def __len__(self):
            return self.level_end - self.level_start

        def __getitem__(self, slc):
            itr = copy.deepcopy(self)
            itr.level_start = self.level_start + slc.start
            itr.level_end = self.level_start + slc.stop
            return itr

        def __iter__(self):
            res_str = tup2str(resolution)
            for i in range(self.level_start, self.level_end):
                cmd = (f"merge_dups {self.storagestr} {i} {dist_thresh}"
                       f" {size_thresh} --voxel_res {res_str}"
                       f" --fulldf_storagestr {output_storagestr}")

                yield SynaptorTask(cmd)

    return MergeDupsTaskIterator(storagestr, hashmax)

def create_remap_tasks(
    cleftpath, cleftoutpath, storagestr,
    bounds, shape, dupstoragestr=None,
    resolution=(8, 8, 40), parallel=1):

    dupstoragestr = storagestr if dupstoragestr is None else dupstoragestr

    shape = Vec(*shape)

    class RemapTaskIterator(object):
        def __init__(self, level_start, level_end):
            self.level_start = level_start
            self.level_end = level_end

        def __len__(self):
            return self.level_end - self.level_start
        def __getitem__(self, slc):
            itr = copy.deepcopy(self)
            itr.level_start = self.level_start + slc.start
            itr.level_end = self.level_start + slc.stop
            return itr

        def __iter__(self):
            self.bounds = bounds.clone()
            self.bounds.minpt.z = bounds.minpt.z + self.level_start * shape.z
            self.bounds.maxpt.z = bounds.minpt.z + self.level_end * shape.z

            for start in xyzrange(self.bounds.minpt, self.bounds.maxpt, shape):
                task_shape = min2(shape.clone(), self.bounds.maxpt - start)

                task_bounds = Bbox(start, start + task_shape)
                if task_bounds.volume() < 1:
                    continue

                chunk_begin = tup2str(task_bounds.minpt)
                chunk_end = tup2str(task_bounds.maxpt)
                res_str = tup2str(resolution)

                cmd = (f"remap_ids {cleftpath} {cleftoutpath} {storagestr}"
                       f" --chunk_begin {chunk_begin} --chunk_end {chunk_end}"
                       f" --dup_map_storagestr {dupstoragestr} --mip {res_str}")

                yield SynaptorTask(cmd)

    level_end = int(math.ceil(bounds.size3().z / shape.z))
    return RemapTaskIterator(0, level_end)


def create_overlap_tasks(
    segpath, base_segpath, storagestr,
    bounds, shape, mip=(8, 8, 40), parallel=1):

    shape = Vec(*shape)

    vol = CloudVolume(segpath, mip=mip)
    # bounds = vol.bbox_to_mip(bounds, mip=0, to_mip=mip)
    bounds = Bbox.clamp(bounds, vol.bounds)

    class OverlapTaskIterator(object):
      def __init__(self, level_start, level_end):
        self.level_start = level_start
        self.level_end = level_end
      def __len__(self):
        return self.level_end - self.level_start
      def __getitem__(self, slc):
        itr = copy.deepcopy(self)
        itr.level_start = self.level_start + slc.start
        itr.level_end = self.level_start + slc.stop
        return itr
      def __iter__(self):
        self.bounds = bounds.clone()
        self.bounds.minpt.z = bounds.minpt.z + self.level_start * shape.z
        self.bounds.maxpt.z = bounds.minpt.z + self.level_end * shape.z

        for startpt in xyzrange( self.bounds.minpt, self.bounds.maxpt, shape ):
          task_shape = min2(shape.clone(), self.bounds.maxpt - startpt)

          task_bounds = Bbox( startpt, startpt + task_shape )
          if task_bounds.volume() < 1:
            continue

          chunk_begin = tup2str(task_bounds.minpt)
          chunk_end = tup2str(task_bounds.maxpt)
          mip_str = tup2str(mip)

          cmd = (f"chunk_overlaps {segpath} {base_segpath} {storagestr}"
                 f" --chunk_begin {chunk_begin} --chunk_end {chunk_end}"
                 f" --parallel {parallel} --mip {mip_str}")

          yield SynaptorTask(cmd)

    level_end = int(math.ceil(bounds.size3().z / shape.z))
    return OverlapTaskIterator(0, level_end)


def create_merge_overlaps_task(storagestr):
    return SynaptorTask(f"merge_overlaps {storagestr}")


def create_cloudvols(
    output_path, temp_output_path, voxelres, vol_shape,
    startcoord, block_shape):

    io.init_seg_volume(output_path, voxelres, vol_shape, "",
                       [], offset=startcoord, chunk_size=block_shape)

    if temp_output_path != output_path:
        io.init_seg_volume(temp_output_path, voxelres, vol_shape, "",
                           [], offset=startcoord, chunk_size=block_shape)
