import numpy as np

from ..proc_tasks import chunk_ccs
from ..proc_tasks import merge_edges
from .. import seg_utils
from .. import io

from . import dataset
from . import score
from . import overlap
from . import toolbox as tb


def full_eval(train_set, val_set, test_set,
              asynet, patchsz=(160,160,18),
              output_prefix=None, write=False,
              voxel_beta=1.5, cleft_beta=1.5, 
              voxel_bins=None, sz_threshs=None,
              dist_thr=1000, voxel_res=[4,4,40]):

    train_set, val_set, test_set = parse_datasets(train_set, val_set, test_set)

    val_set.read()
    print("Tuning parameters on the validation set...")
    (cc_thr, sz_thr,
     _, _, ccs) = tune_parameters_dset(val_set, asynet, patchsz,
                                       voxel_beta, cleft_beta, 
                                       True, #thresh_ccs
                                       voxel_bins, sz_threshs,
                                       dist_thr, voxel_res)
    print("Optimized thresholds: CC {}; Size {}".format(cc_thr, sz_thr))
    val_prec, val_rec, val_precs, val_recs = score_ccs_dset(val_set, ccs)
    print("Validation Set: {0:.3f}P/{1:.3f}R".format(val_prec, val_rec))
    print("Set-Wise: {}".format(list(zip(val_precs, val_recs))))
    val_set.delete()


    if write == True:
        print("Making final ccs for validation set...")
        write_dset_ccs(ccs, output_prefix, "val")


    print("Scoring Training Set...")
    train_set.read()
    ((tr_prec, tr_rec, tr_precs, tr_recs), ccs
    ) = score_w_params(train_set, asynet, patchsz,
                       cc_thr, sz_thr)
    print("Training Set: {0:.3f}P/{1:.3f}R".format(tr_prec, tr_rec))
    print("Set-Wise: {}".format(list(zip(tr_precs, tr_recs))))
    train_set.delete()


    if write == True:
        print("Writing clefts...")
        write_dset_ccs(ccs, output_prefix, "train")


    print("Scoring Test Set...")
    test_set.read()
    ((te_prec, te_rec, te_precs, te_recs), ccs
    ) = score_w_params(test_set, asynet, patchsz,
                       cc_thr, sz_thr)
    print("Test Set: {0:.3f}P/{1:.3f}R".format(te_prec, te_rec))
    print("Set-Wise: {}".format(list(zip(te_precs, te_recs))))
    test_set.delete()


    if write == True:
        print("Writing clefts...")
        write_dset_ccs(ccs, output_prefix, "test")


def parse_datasets(*args):
    dsets = []
    for arg in args:
        if isinstance(arg, dataset.EvalDataset):
            dsets.append(arg)
        else:
            dsets.append(dataset.EvalDataset(arg))

    return dsets


def parse_dataset(arg):
    if isinstance(arg, dataset.EvalDataset):
        return arg
    else:
        dataset.EvalDataset(arg)


def tune_parameters_dset(dset, asynet, patchsz,
                         voxel_beta, cleft_beta, thresh_ccs=False,
                         voxel_bins=None, sz_threshs=None, 
                         dist_thr=1000, voxel_res=[4,4,40]):

    dset = read_dataset(dset)

    print("Tuning Connected Components threshold...")
    ccs, cc_thresh = tune_cc_threshold_dset(dset, voxel_beta, voxel_bins)

    print("Merging duplicates...")
    ccs, _ = merge_duplicate_clefts_dset(asynet, patchsz, dset,
                                         ccs, dist_thr, voxel_res)

    print("Tuning size threshold...")
    sz_thresh, prec, rec, ccs = tune_sz_threshold_dset(dset, ccs, cleft_beta, 
                                                       sz_threshs, 
                                                       thresh_ccs=thresh_ccs)

    return cc_thresh, sz_thresh, prec, rec, ccs


def read_dataset(dset):

    if not isinstance(dset, dataset.EvalDataset):
        dset = dataset.EvalDataset(dset)

    dset.read()

    return dset


def score_w_params(dset, asynet, patchsz, cc_thresh, sz_thresh,
                   dist_thr=1000, voxel_res=[4,4,40]):
    
    dset = read_dataset(dset)

    ccs = make_ccs_dset(dset, cc_thresh)

    ccs, _ = merge_duplicate_clefts_dset(asynet, patchsz, dset,
                                         ccs, dist_thr, voxel_res)
    
    ccs = [seg_utils.filter_segs_by_size(cc, sz_thresh, copy=False)[0]
           for cc in ccs]

    return score_ccs_dset(dset, ccs), ccs


def score_ccs_dset(dset, ccs):

    dset = read_dataset(dset)

    prec, rec, npreds, nlbls = 0., 0., 0, 0
    precs, recs = [], [] #single-vol records
    for (cc, lbl, to_ig) in zip(ccs, dset.labels, dset.to_ignore):
        p, r, npred, nlbl = overlap.score_overlaps(cc, lbl, 
                                                   mode="conservative",
                                                   to_ignore=to_ig)

        old_tp = prec*npreds
        
        npreds += npred
        nlbls += nlbl

        prec = (old_tp + p[0]*npred) / npreds
        rec = (old_tp + r[0]*nlbl) / nlbls

        precs.append(p)
        recs.append(r)

    return prec, rec, precs, recs
        

def tune_cc_threshold_dset(dset, voxel_beta=1.5, voxel_bins=None):

    dset = read_dataset(dset)

    if voxel_bins is None:
        voxel_bins = [0.01*i for i in range(101)]

    tps, fps, fns = analyze_thresholds_dset(dset, voxel_bins)

    cc_thresh = tb.opt_threshold(tps, fps, fns, voxel_beta, voxel_bins)

    ccs = make_ccs_dset(dset, cc_thresh)

    return ccs, cc_thresh


def analyze_thresholds_dset(dset, voxel_bins=None):

    dset = read_dataset(dset)

    if voxel_bins is None:
        voxel_bins = [0.01*i for i in range(101)]

    tps = np.zeros((len(voxel_bins)-1,))
    fps = np.zeros((len(voxel_bins)-1,))
    fns = np.zeros((len(voxel_bins)-1,))

    for (p,l) in zip(dset.preds, dset.labels):
        new_tps, new_fps, new_fns = tb.analyze_thresholds(p,l,voxel_bins)

        tps += new_tps
        fps += new_fps
        fns += new_fns

    return tps, fps, fns


def make_ccs_dset(dset, cc_thresh):
    dset = read_dataset(dset)

    clfs = [chunk_ccs.connected_components3d(pred, cc_thresh)
            for pred in dset.preds]

    return clfs


def merge_duplicate_clefts_dset(asynet, patchsz, dset, ccs,
                                dist_thr=1000, voxel_res=[4,4,40]):

    dset = read_dataset(dset)

    if not isinstance(ccs, list):
        ccs = [ccs]
    assert len(dset) == len(ccs), "mismatched ccs and dset"

    dup_maps = []
    merged_ccs = []
    for (img, seg, clf) in zip(dset.images, dset.segs, ccs):
        new_ccs, new_dup_map = tb.merge_duplicate_clefts(asynet, patchsz,
                                                         img, seg, clf,
                                                         dist_thr, voxel_res)

        dup_maps.append(new_dup_map)
        merged_ccs.append(new_ccs)

    return merged_ccs, dup_maps


def tune_sz_threshold_dset(dset, ccs, beta=1.5, sz_threshs=None, thresh_ccs=False):

    dset = read_dataset(dset)

    if not isinstance(ccs, list):
        ccs = [ccs]
    assert len(dset) == len(ccs), "mismatched ccs and dset"

    if sz_threshs is None:
        sz_threshs = [100*i for i in range(8)]

    sz_threshs = sorted(sz_threshs)
    ccs_c = [np.copy(cc) for cc in ccs]

    n_threshs = len(sz_threshs)
    precs, recs = tb.zero_vec(n_threshs), tb.zero_vec(n_threshs)
    n_preds, n_lbls = tb.zero_vec(n_threshs), tb.zero_vec(n_threshs)
    for (i,sz_thresh) in enumerate(sz_threshs):
        for (cc, lbl, to_ig) in zip(ccs_c, dset.labels, dset.to_ignore):

            cc, _ = seg_utils.filter_segs_by_size(cc, sz_thresh, copy=False)

            prec, rec, npred, nlbl = overlap.score_overlaps(cc, lbl,
                                                            mode="conservative",
                                                            to_ignore=to_ig)

            old_tp = n_preds[i] * precs[i]
            old_tp2 = n_lbls[i] * recs[i] #overly-careful for now
            assert old_tp == old_tp2, "tps don't match for some reason"

            n_preds[i] += npred
            n_lbls[i] += nlbl
            precs[i] = (old_tp + prec[0]*npred) / n_preds[i]
            recs[i] = (old_tp2 + rec[0]*nlbl) / n_lbls[i]


    opt_i = tb.find_best_fscore(precs, recs, beta)

    sz_thresh = sz_threshs[opt_i]
    prec = precs[opt_i]
    rec = recs[opt_i]

    if(thresh_ccs):
        ccs = [seg_utils.filter_segs_by_size(cc, sz_thresh)[0] for cc in ccs]

    return sz_thresh, prec, rec, ccs


def write_dset_ccs(ccs, output_prefix, tag):

    assert output_prefix is not None, "Need output_prefix"

    for (i,cc) in enumerate(ccs):
        fname = "{pref}_{tag}{num}.h5".format(pref=output_prefix, tag=tag, num=i)

        print("Writing {}...".format(fname))
        io.write_h5(cc, fname)