"""Canonical examples on create tasks."""

import os
import subprocess
import time

from gcloud import storage
from gcloud import credentials
from oauth2client.client import GoogleCredentials
from pipeline.tasks import task_base

DEFAULT_PROJECT = 'vkit-pipeline'

def get_credential():
    return GoogleCredentials.get_application_default()

class BlobUploadTask(task_base.TaskBase):
    """A task which uploads file to Google Cloud Storage.

    It requires authentication be properly configured."""

    default_provides = ('bucket', 'path', 'public_url')

    def execute(
            self, bucket_name, src_path, dest_path, project=DEFAULT_PROJECT):
        print "Start blob upload"
        client = storage.Client(project=project, credentials=get_credential())
        bucket = client.get_bucket(bucket_name)
        blob = bucket.blob(dest_path)
        blob.upload_from_filename(filename=src_path)
        print "Uploaded to %s" % blob.public_url
        return bucket_name, dest_path, blob.public_url


class BlobDownloadTask(task_base.TaskBase):
  """A task which downloads file to Google Cloud Storage.

  It requires authentication be properly configured."""

  def execute(self, bucket_name, path, output_dir, project=DEFAULT_PROJECT):
    client = storage.Client(project=project, credentials=get_credential())
    bucket = client.get_bucket(bucket_name)
    blob = bucket.get_blob(path)
    if blob:
      filename = output_dir + '/' + path
      if not os.path.exists(os.path.dirname(filename)):
        try:
          os.makedirs(os.path.dirname(filename))
        except:
          raise
      with open(filename, "w") as f:
        blob.download_to_file(f)
        print 'File downloaded to %s' % f.name
    else:
        print 'Cannot find the output from GCS.'


class CompressOutputDirTask(task_base.TaskBase):
    """Compress pipeline output_dir into a file which can be uploaded to GCS.

    Normally be used as the final step for pipeline job to return generated
    content to its poster."""

    def execute(self, output_dir, tarfile):
        subprocess.call(
            ['tar', '-zcvf', tarfile, output_dir])

    # TODO(cbao): check tar exists
