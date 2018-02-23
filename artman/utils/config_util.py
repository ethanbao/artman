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

"""Utils related to config files"""

from __future__ import absolute_import
import collections
import io

from ruamel import yaml

import six


def load_config_spec(config_spec, config_sections, repl_vars, language):
    config_split = config_spec.strip().split(':')
    config_path = config_split[0]
    if len(config_split) > 1:
        config_sections = config_split[1].split('|')
    with io.open(config_path, encoding='UTF-8') as config_file:
        all_config_data = yaml.load(config_file, Loader=yaml.Loader)

    # Make a list of the appropriate configuration sections (just the ones
    # we are actually using) from the YAML file.
    segments = [all_config_data[i] for i in config_sections]
    segments.append(all_config_data.get(language, {}))

    # Merge all of the segments of data into a single config dictionary.
    config = merge(*segments)

    # Perform final replacements.
    return replace_vars(config, repl_vars)


def merge(*dictionaries):
    """Recursively merge one or more dictionaries together.

    Conflict resolution:
        - Dictionary values are recursively merged.
        - List values are added together.
        - Sets are combined with `__or__` (equivalent of set.union).
        - Everything else, the right-most value wins.
        - If values are type mismatches in the above resolution rules,
          raise ValueError.

    Args:
        *dictionaries (list(dict)): Dictionaries to be merged.

    Returns:
        dict: The merged dictionary.
    """
    answer = {}
    for d in dictionaries:
        for k, v in six.iteritems(d):
            # If the value is not yet found in the answer, then
            # simply add it and move on.
            if k not in answer:
                answer[k] = v
                continue

            # If the values are both lists, append them to one another.
            if isinstance(answer[k], list):
                if not isinstance(v, list):
                    raise ValueError('Attempt to merge a list and non-list.')
                answer[k] = answer[k] + v
                continue

            # If the values are both sets, take their union.
            if isinstance(answer[k], set):
                if not isinstance(v, set):
                    raise ValueError('Attempt to merge a set and non-set.')
                answer[k] = answer[k] | (v)
                continue

            # If the values are both dictionaries, merge them recursively
            # by calling this method.
            if isinstance(answer[k], dict):
                if not isinstance(v, dict):
                    raise ValueError('Attempt to merge a dict and non-dict.')
                answer[k] = merge(answer[k], v)
                continue

            # These are either primitives or other objects; the right-hand
            # value wins.
            answer[k] = v
    return answer


def replace_vars(data, repl_vars):
    """Return the given data structure with appropriate variables replaced.

    Args:
        data (Any): Data of an arbitrary type.

    Returns:
        Any: Data of the same time, possibly with replaced values.
    """
    if isinstance(data, (six.text_type, six.binary_type)):
        for k, v in repl_vars.items():
            data = data.replace('${' + k + '}', v)
        return data
    if isinstance(data, collections.Sequence):
        return type(data)([replace_vars(d, repl_vars) for d in data])
    if isinstance(data, collections.Mapping):
        return type(data)([(k, replace_vars(v, repl_vars))
                           for k, v in data.items()])
    return data
