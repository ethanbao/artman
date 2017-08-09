# Copyright 2016 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The new artman CLI with the following syntax.

    artman --config <artman_config_yaml> [Options] generate|publish [--publish-target=<public_target_name>] <artifact_name>

Note: Only local execution is supported as this moment. The CLI syntax is beta,
and might have changes in the future.
"""

from __future__ import absolute_import
from logging import DEBUG, INFO
import argparse
import ast
import base64
import io
import os
import subprocess
import sys
import tempfile
import time
import uuid

from ruamel import yaml

from taskflow import engines

from artman.config import converter, reader
from artman.config.proto import config_pb2
from artman.cli import support
from artman.pipelines import pipeline_factory
from artman.utils import job_util, pipeline_util, config_util
from artman.utils.logger import logger, setup_logging


def main(*args):
    # If no arguments are sent, we are using the entry point; derive
    # them from sys.argv.
    if not args:
        args = sys.argv[1:]

    # Get to a normalized set of arguments.
    flags = parse_args(*args)
    user_config = read_user_config(flags)
    pipeline_name, pipeline_kwargs = normalize_flags(flags, user_config)

    if flags.no_docker:
        print('Running artman locally.')
        pipeline = pipeline_factory.make_pipeline(
            pipeline_name, False, **pipeline_kwargs)
        # Hardcoded to run pipeline in serial engine, though not necessarily.
        engine = engines.load(pipeline.flow, engine='serial',
                              store=pipeline.kwargs)
        engine.run()
    else:
        print('Running artman command in docker.')
        _run_artman_in_docker(flags)



def parse_args(*args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--config',
        type=str,
        default=None,
        required=True,   # This could eventually become optional.
        help='Specify path to artman config yaml.',
    )
    parser.add_argument(
        '--output_dir',
        type=str,
        default='/tmp/artman/output',
        help='Local path to the googleapis repository. Can also be set in '
             'the user config file under local_paths.googleapis. If not set, '
             'defaults to ${reporoot}/googleapis',
    )
    parser.add_argument(
        '--input_dir',
        type=str,
        default='.',
        help='Local path to the googleapis repository. Can also be set in '
             'the user config file under local_paths.googleapis. If not set, '
             'defaults to ${reporoot}/googleapis',
    )
    parser.add_argument('-v', '--verbose',
        action='store_const',
        const=10,
        default=None,
        dest='verbosity',
        help='Show verbose / debug output.',
    )
    parser.add_argument('--user-config',
        default='~/.artman/config.yaml',
        help='User configuration file for artman. Stores GitHub credentials.',
    )
    parser.add_argument('--no-docker',
        dest='no_docker',
        action='store_true',
        help='If specified, running the artman on the host machine instead of '
             'artman docker instance.',
    )
    parser.set_defaults(no_docker=False)

    # Add sub-commands.
    subparsers = parser.add_subparsers(
        dest='subcommand',
        help='Support [generate|publish] sub-commands'
    )

    # `generate` sub-command.
    parser_generate = subparsers.add_parser(
        'generate',
        help='Generate artifact')
    parser_generate.add_argument(
        'artifact_id',
        type=str,
        help='The artifact id specified in the artman config file.'
    )

    # `publish` sub-command.
    parser_publish = subparsers.add_parser(
        'publish',
        help='Publish artifact')
    parser_publish.add_argument(
        'artifact_id',
        type=str,
        help='The artifact id specified in the artman config file.'
    )
    parser_publish.add_argument('--publish-target',
        type=str,
        default=None,
        required=True,
        help='Set where to publish the code. It is defined as publishing '
             'target in the artman config',
    )
    parser_publish.add_argument('--github-username',
        default=None,
        help='The GitHub username. Must be set if publishing, but can come '
             'from the user config file.',
    )
    parser_publish.add_argument('--github-token',
        default=None,
        help='The GitHub token (or password, but do not do that). Must be set '
             'if publishing, but can come from the user config file.',
    )

    return parser.parse_args(args=args)


def read_user_config(flags):
    """Read the user config from disk and return it.

    Args:
        flags (argparse.Namespace): The flags from sys.argv.

    Returns:
        dict: The user config.
    """
    # Load the user configuration if it exists and save a dictionary.
    user_config = {}
    user_config_file = os.path.realpath(os.path.expanduser(flags.user_config))
    if os.path.isfile(user_config_file):
        with io.open(user_config_file) as ucf:
            user_config = yaml.load(ucf.read(), Loader=yaml.Loader) or {}

    # Sanity check: Is there a configuration? If not, abort.
    if not user_config:
        setup_logging(INFO)
        logger.critical('No user configuration found.')
        logger.warn('This is probably your first time running Artman.')
        logger.warn('Run `configure-artman` to get yourself set up.')
        sys.exit(64)

    # Done; return the user config.
    return user_config

def normalize_flags(flags, user_config):
    """Combine the argparse flags and user configuration together.

    Args:
        flags (argparse.Namespace): The flags parsed from sys.argv
        user_config (dict): The user configuration taken from ~/.artman/config.yaml.

    Returns:
        tuple (str, dict): 2-tuple containing:
            - pipeline name
            - pipeline arguments
    """
    pipeline_args = {}

    # Determine logging verbosity and then set up logging.
    verbosity = support.resolve('verbosity', user_config, flags, default=INFO)
    setup_logging(verbosity)

    # Save local paths, if applicable.
    # This allows the user to override the path to api-client-staging or
    # toolkit on his or her machine.
    pipeline_args['local_paths'] = support.parse_local_paths(user_config, flags.input_dir)

    artman_config_path = flags.config
    if not os.path.isfile(artman_config_path):
        logger.error('Artman config file `%s` doesn\'t exist.' % artman_config_path)
        sys.exit(96)

    artifact_config = reader.read_artifact_config(artman_config_path, flags)

    # If we were given just an API or BATCH, then expand it into the --config
    # syntax.
    shared_config_name = 'common.yaml'
    if artifact_config.language == config_pb2.Artifact.RUBY:
        # TODO(ethanbao): Figure out why.
        shared_config_name = 'doc.yaml'


    legacy_config_dict = converter.convert_to_legacy_config_dict(artifact_config, flags.input_dir, flags.output_dir)
    tmp_legacy_config_yaml = '%s.tmp' % artman_config_path
    with open(tmp_legacy_config_yaml, 'w') as outfile:
      yaml.dump(legacy_config_dict, outfile, default_flow_style=False)


    googleapis = os.path.realpath(os.path.expanduser(
        pipeline_args['local_paths']['googleapis'],
    ))
    config = ','.join([
        '{artman_config_path}',
        '{googleapis}/gapic/lang/{shared_config_name}',
    ]).format(
        artman_config_path=tmp_legacy_config_yaml,
        googleapis=googleapis,
        shared_config_name=shared_config_name,
    )

    # Set the pipeline as well as package_type and packaging
    artifact_type = artifact_config.type
    if artifact_type == config_pb2.Artifact.GAPIC:
        pipeline_name = 'GapicClientPipeline'
    elif artifact_type == config_pb2.Artifact.GAPIC_CONFIG:
        pipeline_name = 'GapicConfigPipeline'
    elif artifact_type == config_pb2.Artifact.GRPC:
        pipeline_name = 'GrpcClientPipeline'
    elif artifact_type == config_pb2.Artifact.GRPC_COMMON:
        pipeline_name = 'GrpcClientPipeline'
    elif artifact_type == config_pb2.Artifact.GAPIC_ONLY:
        pipeline_name = 'GapicClientPipeline'

    language = config_pb2.Artifact.Language.Name(artifact_config.language).lower()
    pipeline_args['language'] = language

    # Parse out the full configuration.
    # Note: the var replacement is still needed because they are still being
    # used in some shared/common config yamls.
    config_sections = ['common']
    for config_spec in config.split(','):
        config_args = config_util.load_config_spec(
            config_spec=config_spec,
            config_sections=config_sections,
            repl_vars={k.upper(): v for k, v in
                       pipeline_args['local_paths'].items()},
            language=language,
        )
        pipeline_args.update(config_args)

    # Setup publishing related config if needed.
    if flags.subcommand == 'generate':
        pipeline_args['publish'] = 'noop'
    elif flags.subcommand == 'publish':
        publishing_config = _get_publishing_config(artifact_config, flags.publish_target)
        if publishing_config.type == config_pb2.Artifact.PublishTarget.GITHUB:
            pipeline_args['publish'] = 'github'
            pipeline_args['github'] = support.parse_github_credentials(
                argv_flags=flags,
                config=user_config.get('github', {}),
            )
            repos = pipeline_args.pop('git_repos')
            pipeline_args['git_repo'] = support.select_git_repo(repos, publishing_config.name)
        else:
            raise NameError('Publishing type `%s` is not supported yet.' % config_pb2.Artifact.PublishTarget.Type.Name(publishing_config.type))

    # Print out the final arguments to stdout, to help the user with
    # possible debugging.
    pipeline_args_repr = yaml.dump(pipeline_args,
        block_seq_indent=2,
        default_flow_style=False,
        indent=2,
    )
    logger.info('Final args:')
    for line in pipeline_args_repr.split('\n'):
        if 'token' in line:
            index = line.index(':')
            line = line[:index + 2] + '<< REDACTED >>'
        logger.info('  {0}'.format(line))

    # Clean up the tmp legacy artman config.
    os.remove(tmp_legacy_config_yaml)

    # Return the final arguments.
    # This includes a pipeline to run, arguments, and whether to run remotely.
    return (
        pipeline_name,
        pipeline_args
    )


def _get_publishing_config(artifact_config_pb, publish_target):
    valid_options = []
    for target in artifact_config_pb.publish_targets:
        valid_options.append(target.name)
        if target.name == publish_target:
            return target
    raise KeyError('No publish target with `%s` configured in artifact `%s`. Valid options are %s' % (publish_target, artifact_config_pb.name, valid_options))

def _run_artman_in_docker(flags):
  """Executes artman command.

  Args:
    input_dir: The input directory that will be mounted to artman docker
        container as local googleapis directory.
  Returns:
    The output directory with artman-generated files.
  """
  ARTMAN_CONTAINER_NAME = 'artman-docker'
  input_dir = os.path.abspath(flags.input_dir)
  output_dir = os.path.abspath(flags.output_dir)
  artman_config = os.path.abspath(flags.config)
  docker_image = 'f5d7613b91ce'  #FLAGS.docker_image

  inner_artman_cmd_str = ' '.join(sys.argv[1:])
  inner_artman_cmd_str= inner_artman_cmd_str.replace(flags.config, artman_config)
  print(inner_artman_cmd_str)

  # TODO(ethanbao): A trivial folder to folder mounting won't work on windows.
  base_cmd = [
      'docker', 'run', '--name', ARTMAN_CONTAINER_NAME, '--rm', '-i', '-t',
      #'-e', 'LOCAL_USER_ID=%s' % os.getuid(),
      #'-u', '%s:%s' % (os.getuid(), os.getgid()),
      '-e', 'GOSU_USER=%s:%s' % (os.getuid(), os.getgid()), '-e', 'GOSU_CHOWN=%s' % output_dir,
      '-v', '%s:%s' % (input_dir, input_dir),
      '-v', '%s:%s' % (output_dir, output_dir),
      '-v', '%s:%s' % (os.path.dirname(artman_config), os.path.dirname(artman_config)),
      #'-v', '%s:/home/.artman' % os.path.dirname(os.path.abspath(flags.user_config)),
      docker_image,
      '/bin/bash',
      '-c']

  #inner_artman_cmd_str = 'artman2 %s' % raw_args)
  cmd = base_cmd
  cmd.append('artman2 --no-docker --user-config=/home/.artman/config.yaml %s' % (inner_artman_cmd_str))

  debug_cmd = list(base_cmd)
  debug_cmd.append('"%s; bash"' % inner_artman_cmd_str)

  try:
    output = subprocess.check_output(cmd)
    print(output.decode('utf8'))
    return output_dir
  except subprocess.CalledProcessError as e:
    print(e.output.decode('utf8'))
    print('Artman execution failed. For additional logging, re-run the '
          'command with the "--verbose" flag')
    raise
  finally:
    print('For further inspection inside docker container, run `%s`' %
          ' '.join(debug_cmd))


if __name__ == "__main__": main()
