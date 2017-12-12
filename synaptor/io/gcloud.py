#!/usr/bin/env python3


import os

#Piggybacking on cloudvolume's secrets
import cloudvolume
from google.cloud import storage

from . import utils


PROJECT_NAME = cloudvolume.secrets.PROJECT_NAME
CREDS        = cloudvolume.secrets.google_credentials


def send_local_file(local_name, remote_path):
    bucket, key = parse_remote_path(remote_path)

    blob = open_bucket(bucket).blob(key)

    blob.upload_from_filename(local_name)


def send_local_dir(local_dir, remote_dir):
    bucket, key = parse_remote_path(remote_dir)

    #Sending directory to a subdirectory of remote dir
    key = os.path.join(os.path.basename(utils.check_no_slash(local_dir)))

    fnames = os.listdir(local_dir)
    remote_keys = [os.path.join(key, f) for f in fnames]

    active_bucket = open_bucket(bucket)

    for (f,key) in zip(fnames, remote_keys):
        blob = active_bucket.blob(key)
        blob.upload_from_filename(os.path.join(local_dir, f))


def pull_file(remote_path):
    bucket, key = parse_remote_path(remote_path)

    local_fname = os.path.basename(remote_path)

    blob = open_bucket(bucket).blob(key)

    blob.download_to_filename(local_fname)

    return local_fname


def pull_all_files(remote_dir):
    """ This will currently break if the remote dir has subdirectories """
    bucket, key = parse_remote_path(remote_dir)

    active_bucket = open_bucket(bucket)

    remote_blobs = list(active_bucket.list_blobs(prefix = utils.check_slash(key)))
    local_dir    = os.path.basename(utils.check_no_slash(key))
    local_fnames = [os.path.join(local_dir, os.path.basename(b.name))
                    for b in remote_blobs]

    if not os.path.isdir(local_dir):
        os.makedirs(local_dir)

    for (f,b) in zip(local_fnames, remote_blobs):
        b.download_to_filename(f)

    return local_fnames


def parse_remote_path(remote_path):
    protocol, bucket, key = utils.parse_remote_path(remote_path)

    assert protocol == "gs:", "Mismatched protocol (expected Google Storage)"

    return bucket, key


def open_bucket(bucket):
    client = storage.Client(project=PROJECT_NAME,
                            credentials=CREDS)

    return client.get_bucket(bucket)