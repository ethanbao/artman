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

"""Artman config converter.

It converts the legacy artman config file into the new format. This CLI can be
removed after all current artman users migrate to the new artman config format.
"""

from __future__ import absolute_import
import argparse
import io
import json
import re
import sys
import os
import yaml


from collections import OrderedDict

from artman.config.proto import config_pb2
from google.protobuf.json_format import MessageToJson


def main(*args):
    if not args:
        args = sys.argv[1:]

    # Get to a normalized set of arguments.
    flags = parse_args(*args)
    legacy_config = _load_legacy_config_dict(os.path.abspath(flags.config))
    new_config = _convert(legacy_config)
    _write_pb_to_yaml(new_config, flags.output)


def parse_args(*args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'config',
        type=str,
        help='Path to the legacy artman config')
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='If specified, the converted yaml will be stored in the specified '
             'output file. Otherwise, the converter will only print out the '
             'result.')
    return parser.parse_args(args=args)


def _convert(legacy_config):
    # Compute common section
    result = config_pb2.Config()
    result.common.api_name = legacy_config['common']['api_name']
    result.common.api_version = legacy_config['common']['api_version']
    result.common.organization_name = legacy_config[
        'common']['organization_name']
    if 'gapic_api_yaml' inlegacy_config['common']:
        result.common.gapic_yaml = _sanitize_repl_var(
            legacy_config['common']['gapic_api_yaml'][0])
    result.common.service_yaml = _sanitize_repl_var(
        legacy_config['common']['service_yaml'][0])
    if 'proto_deps' in legacy_config['common']:
        result.common.proto_deps.extend(_compute_deps(
            legacy_config['common']['proto_deps']))
    if 'proto_test_deps' in legacy_config['common']:
        result.common.test_proto_deps.extend(_compute_deps(
            legacy_config['common']['proto_test_deps']))
    result.common.src_proto_paths.extend(
        _compute_src_proto_paths(legacy_config['common']['src_proto_path']))

    # Compute artifacts section
    common_git_repos = legacy_config['common']['git_repos']
    LANGS = ['java', 'python', 'php', 'ruby', 'go', 'csharp', 'nodejs']
    for lang in LANGS:
        if lang not in legacy_config:
            continue
        legacy_artifact_config = legacy_config[lang]
        if legacy_artifact_config:
            artifact = config_pb2.Artifact()
            artifact.name = '%s_gapic' % lang
            artifact.language = config_pb2.Artifact.Language.Value(lang.upper())

            if 'release_level' in legacy_artifact_config:
                artifact.release_level = config_pb2.Artifact.ReleaseLevel.Value(
                    legacy_artifact_config['release_level'].upper())

            artifact.type = _compute_artifact_type(legacy_config)

            # Compute package version.
            if 'generated_package_version' in legacy_artifact_config:
                legacy_package_version = legacy_artifact_config[
                    'generated_package_version']
                if 'lower' in legacy_package_version:
                    artifact.package_version.grpc_dep_lower_bound = (
                        legacy_package_version['lower'])
                if 'upper' in legacy_package_version:
                    artifact.package_version.grpc_dep_upper_bound = (
                        legacy_package_version['upper'])

            # Compute publishing targets
            if 'git_repos' in legacy_artifact_config:
                artifact.publish_targets.extend(_compute_publish_targets(
                    legacy_artifact_config['git_repos'], common_git_repos))
            result.artifacts.extend([artifact])

    return result

def _compute_package_version(legacy_package_version):
    result = config_pb2.Artifact.PackageVersion()
    if 'lower' in legacy_package_version:
        result.grpc_dep_lower_bound = legacy_package_version['lower']
    if 'upper' in legacy_package_version:
        result.grpc_dep_upper_bound = legacy_package_version['upper']
    return result


def _compute_artifact_type(legacy_config):
    if legacy_config['common']['packaging'] == 'google-cloud':
        return config_pb2.Artifact.GAPIC_ONLY
    elif legacy_config['common']['package_type'] == 'grpc_common':
        return config_pb2.Artifact.GRPC_COMMON

    return config_pb2.Artifact.GAPIC


def _compute_publish_targets(git_repos, common_git_repos):
    result = []
    for k, v in git_repos.items():
        location = (common_git_repos[k]['location']
                    if k in common_git_repos.keys() else v['location'])
        target = config_pb2.Artifact.PublishTarget()
        target.name = k
        target.location = location
        target.type = config_pb2.Artifact.PublishTarget.GITHUB

        if 'paths' in v:
            target.directory_mappings.extend(
                _compute_directory_mappings(v['paths']))
        result.append(target)
    return result

def _compute_directory_mappings(paths):
    result = []
    for path in paths:
      mapping = config_pb2.Artifact.PublishTarget.DirectoryMapping()
      if isinstance(path, str):
          mapping.dest = path
      else:
          if 'src' in path:
              mapping.src = path['src']
          if 'dest' in path:
              mapping.dest = path['dest']
          if 'artifact' in path:
              mapping.name = path['artifact']
          result.append(mapping)
    return result

def _compute_deps(proto_deps):
    result = []
    for dep in proto_deps:
        new_dep = config_pb2.Artifact.ProtoDependency()
        new_dep.name = dep
        result.append(new_dep)
    return result

def _compute_src_proto_paths(src_proto_paths):
    result = []
    for src_proto_path in src_proto_paths:
        result.append(_sanitize_repl_var(src_proto_path))
    return result

def _sanitize_repl_var(value):
    if value.startswith('${GOOGLEAPIS}/'):
        return value.replace('${GOOGLEAPIS}/', '')
    if value.startswith('${REPOROOT}/'):
        return value.replace('${REPOROOT}/', '')

def camel_to_underscore(name):
    camel_pat = re.compile(r'([A-Z])')
    return camel_pat.sub(lambda x: '_' + x.group(1).lower(), name)

def convert_json(d):
    """Convert the dict to turn all key into lower underscore case."""
    new_d = {}
    for k, v in d.items():
        if isinstance(v, dict):
            new_d[camel_to_underscore(k)] = convert_json(v)
        elif isinstance(v, list):
            if isinstance(v[0], dict):
                result = []
                for d2 in v:
                    result.append(convert_json(d2))
                new_d[camel_to_underscore(k)] = result
            else:
                new_d[camel_to_underscore(k)] = v
        else:
            new_d[camel_to_underscore(k)] = v
    return new_d

def _write_pb_to_yaml(pb, output):
    # Add yaml representer so taht yaml dump can dump OrderedDict. The code
    # is coming from https://stackoverflow.com/questions/16782112.
    yaml.add_representer(OrderedDict, represent_ordereddict)

    json_obj = _order_dict(convert_json(json.loads(MessageToJson(pb))))
    if output:
        with open(output, 'w') as outfile:
            yaml.dump(json_obj, outfile, default_flow_style=False)
        print('Check the converted yaml at %s' % output)
    else:
        print(yaml.dump(json_obj, default_flow_style=False))

def represent_ordereddict(dumper, data):
    value = []
    for item_key, item_value in data.items():
        node_key = dumper.represent_data(item_key)
        node_value = dumper.represent_data(item_value)

        value.append((node_key, node_value))

    return yaml.nodes.MappingNode(u'tag:yaml.org,2002:map', value)

def _order_dict(od):
    # The whole key order is flattened which is okay for artman config because
    # the order of fields in the nested message types doesn't conflict with
    # the top-level one.
    keyorder = [
        'common', 'artifacts', 'name', 'api_name', 'api_version',
        'organization_name', 'service_yaml', 'gapic_yaml',
        'src_proto_paths', 'proto_deps', 'test_proto_deps',
        'type', 'language', 'release_level', 'package_version',
        'publish_targets', 'location', 'directory_mappings', 'src', 'dest',
        'grpc_dep_lower_bound', 'grpc_dep_upper_bound'
    ]
    res = OrderedDict()
    for k, v in sorted(od.items(), key=lambda i:keyorder.index(i[0])):
        if isinstance(v, dict):
            res[k] = _order_dict(v)
        elif isinstance(v, list):
            if isinstance(v[0], dict):
                result = []
                for d2 in v:
                    result.append(_order_dict(d2))
                res[k] = result
            else:
                res[k] = v
        else:
            res[k] = v
    return res

def _load_legacy_config_dict(path):
    with io.open(path, 'r') as yaml_file:
        return yaml.load(yaml_file)

if __name__ == "__main__":
    main()
