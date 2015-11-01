#!/usr/bin/env python

import argparse
import copy
import datetime
import difflib
import json
import os
import re
import sys

import datadog
import datadog.api
import pytz
import yaml

import bcolors


PROGNAME = 'dogpush'

US_PACIFIC = pytz.timezone('US/Pacific')

EPOCH = US_PACIFIC.localize(datetime.datetime.fromtimestamp(0))


class DogPushException(Exception):
    pass


def _load_config(config_file):
    with open(config_file, 'r') as f:
        config = yaml.load(f)
    if 'teams' not in config:
        config['teams'] = {}
    if 'datadog' not in config:
        config['datadog'] = {}
    config['datadog']['app_key'] = config['datadog'].get(
            'app_key', os.getenv('DATADOG_APP_KEY'))
    config['datadog']['api_key'] = config['datadog'].get(
            'api_key', os.getenv('DATADOG_API_KEY'))
    if 'default_rule_options' not in config:
        config['default_rule_options'] = LOCAL_DEFAULT_RULE_OPTIONS

    # Ensure the keys of the above two groups are disjoint.
    assert(set(config['default_rule_options'].keys()) &
           set(DATADOG_DEFAULT_OPTIONS.keys()) == set())

    return config


SEVERITIES = ['CRITICAL', 'WARNING', 'INFO']


SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))


# Datadog fields we do not store locally.
IGNORE_FIELDS = ['created_at', 'creator', 'org_id', 'overall_state', 'id',
                 # dogpush specific:
                 'business_hours_only', 'team', 'severity']

# Datadog fields that we do not store in our monitor rules if they have the
# default value.
# There are two type of defaults: defaults coming from TrueAccord (all
# our rules get these values by default) and defaults coming from DataDog.
LOCAL_DEFAULT_RULE_OPTIONS = {
  'notify_no_data': True,
  'renotify_interval': 15
}

DATADOG_DEFAULT_OPTIONS = {
  'notify_audit': False,
  'silenced': {}
}


def _pretty_yaml(d):
    return re.sub('^-', '\n-', yaml.dump(d), flags=re.M)


def current_time():
    return datetime.datetime.now(US_PACIFIC)


# Transform a monitor to a canonical form by removing defaults
def _canonical_monitor(original, default_team=None, **kwargs):
    m = copy.deepcopy(original)
    if 'tags' in m and not m['tags']:
        del m['tags']
    for field in IGNORE_FIELDS:
        m.pop(field, None)
    all_defaults = (DATADOG_DEFAULT_OPTIONS.items() + 
                    CONFIG['default_rule_options'].items())
    for (field, value) in all_defaults:
        if field in m['options'] and m['options'][field] == value:
            del m['options'][field]
    m['name'] = m['name'].strip()
    original_team = original.get('team')
    team = original_team if original_team is not None else default_team
    severity = original.get('severity') or 'CRITICAL'
    if team:
        if isinstance(team, basestring):
            team = [team]
        m['message'] = m.get('message', '')
        for t in team:
            dogpush_line = CONFIG['teams'][t]['notifications'][severity]
            m['message'] += ('\n' if m['message'] else '') + dogpush_line

    result = dict(
        name = m['name'],
        id = original.get('id'),
        obj = m,
        business_hours_only = original.get('business_hours_only')
    )
    result.update(kwargs)
    return result


def get_datadog_monitors():
    monitors = datadog.api.Monitor.get_all()
    if not _check_monitor_names_unique(monitors):
        raise DataDogException(
            'Duplicate names found in remote datadog monitors.')
    result = {}
    for m in monitors:
        m = _canonical_monitor(m)
        result[m['name']] = m
    return result


def _check_monitor_names_unique(monitors):
    names = [m['name'] for m in monitors]
    if (len(names) != len(set(names))):
        counts = {}
        for name in names:
            counts[name] = counts.get(name, 0) + 1
            if counts[name] > 1:
                print >> sys.stderr, "Duplicate name: %s" % name
        return False
    return True


def _check_monitor(monitor, location):
    name = monitor.get('name', '')
    if isinstance(name, basestring):
        name = name.strip()
    if not name:
        raise DogPushException('%s: found monitor without a name' % location)


def get_local_monitors():
    l = CONFIG.get('rule_files', [])
    monitors = []
    for filename in l:
        with open(filename, 'r') as f:
            r = yaml.load(f)
            if r is None:
                r = {alerts: []}
            if not isinstance(r, dict):
                raise DogPushException("Expected a dictionary")
            if not isinstance(r.get('alerts'), list):
                raise DogPushException("'alerts' must be a list of alerts.")
            default_team = r.get('team')
            for monitor in r['alerts']:
                _check_monitor(monitor, filename)
                monitors.append(_canonical_monitor(monitor, filename=filename,
                                                   default_team=default_team))
    if not _check_monitor_names_unique(monitors):
        raise DataDogException('Duplicate names found in local monitors.')
    result = dict((m['name'], m) for m in monitors)
    return result


def _prepare_monitor(m):
    obj = copy.deepcopy(m['obj'])
    for (key, value) in CONFIG['default_rule_options']:
        if 'options' not in obj:
            obj['options'] = {}
        if key not in obj['options']:
            obj['options'][key] = value
    return obj


def _is_changed(local, remote):
    # For a business hours only alert, we ignore silencing when comparing.
    # TODO(nadavsr): rethink how silencing should affect monitors in general.
    if local['business_hours_only']:
        remote['obj']['options'].pop('silenced', None)

    return local['obj'] != remote['obj']


def command_push():
    local_monitors = get_local_monitors()
    remote_monitors = get_datadog_monitors()

    only_local = set(local_monitors.keys()) - set(remote_monitors.keys())
    if only_local:
        print "Pushing %d new monitors." % len(only_local)
        for name in only_local:
            datadog.api.Monitor.create(**_prepare_monitor(local_monitors[name]))

    common_names = set(local_monitors.keys()) & set(remote_monitors.keys())
    changed = [name for name in common_names
               if _is_changed(local_monitors[name], remote_monitors[name])]
    if changed:
        print "Updating %d modified alerts" % len(changed)
        for name in changed:
            datadog.api.Monitor.update(
                remote_monitors[name]['id'],
                **_prepare_monitor(local_monitors[name]))


def _is_business_hours(now = None):
    now = now or current_time()
    return (9 <= now.hour <= 15) and (now.weekday() not in (5, 6))


def _next_business_hour(now = None):
    """Returns the next time it is business hours."""
    # Adds one hour until _is_business_hours() return true.  This is
    # actually pretty fast (less than 1ms), and avoids complex calculation.
    start = now = now or current_time()
    one_hour = datetime.timedelta(hours=1)
    while not _is_business_hours(now):
        now += one_hour
    # Round to the start of the hour.
    if start != now:
        now -= datetime.timedelta(minutes=now.minute, seconds=now.second)
    return now


def command_mute():
    local_monitors = get_local_monitors()
    remote_monitors = get_datadog_monitors()
    if _is_business_hours():
        print "It is business hours now. Nothing to do."
    timestamp = (_next_business_hour() - EPOCH).total_seconds()

    for monitor in local_monitors.values():
        if monitor['business_hours_only']:
            remote = remote_monitors[monitor['name']]
            if 'silenced' in remote['obj']['options']:
                print "Alert '%s' already muted. Skipping." % monitor['name']
            else:
                id = remote['id']
                datadog.api.Monitor.mute(id, end=timestamp)


def command_diff():
    local_monitors = get_local_monitors()
    remote_monitors = get_datadog_monitors()

    only_local = set(local_monitors.keys()) - set(remote_monitors.keys())
    only_remote = set(remote_monitors.keys()) - set(local_monitors.keys())
    common_names = set(local_monitors.keys()) & set(remote_monitors.keys())
    changed = [name for name in common_names
               if _is_changed(local_monitors[name],
                  remote_monitors[name])]

    if only_local:
        sys.stdout.write(bcolors.WARNING)
        print '---------------------------------------------------------'
        print ' NEW MONITORS.  These monitors are currently missing in'
        print ' datadog and can be pushed using "%s push"' % PROGNAME
        print '---------------------------------------------------------'
        sys.stdout.write(bcolors.ENDC)
        monitors = [local_monitors[name]['obj'] for name in only_local]
        print _pretty_yaml(monitors)
    if changed:
        sys.stdout.write(bcolors.WARNING)
        print '---------------------------------------------------------'
        print ' TO BE UPDATED.  These monitors exist in datadog, but are'
        print ' different than the local version.  Use "%s push"' % PROGNAME
        print ' to push them to datadog.'
        print '---------------------------------------------------------'
        print
        sys.stdout.write(bcolors.ENDC)
        for name in changed:
            remote_name = 'datadog:%s' % name
            local_name = '%s:%s' % (local_monitors[name]['filename'], name)
            for line in difflib.unified_diff(
                    _pretty_yaml(remote_monitors[name]['obj']).splitlines(True),
                    _pretty_yaml(local_monitors[name]['obj']).splitlines(True),
                    fromfile=remote_name, tofile=local_name):
                if line.startswith('---') or line.startswith('+++'):
                    sys.stdout.write(bcolors.BOLD + line + bcolors.ENDC)
                elif line.startswith('-'):
                    sys.stdout.write(bcolors.RED + line + bcolors.ENDC)
                elif line.startswith('+'):
                    sys.stdout.write(bcolors.GREEN + line + bcolors.ENDC)
                else:
                    sys.stdout.write(line)
    if only_remote:
        sys.stdout.write(bcolors.WARNING)
        print '------------------------------------------------------------'
        print ' UNTRACKED MONITORS.  These monitors are only in datadog    '
        print ' and needed to be MANUALLY added to a local file or removed '
        print ' from datadog.                                              '
        print '------------------------------------------------------------'
        sys.stdout.write(bcolors.ENDC)
        monitors = [remote_monitors[name]['obj'] for name in only_remote]
        print _pretty_yaml(monitors)
        sys.stdout.write(bcolors.FAIL)
        print "*** FAILED *** Untracked monitors found."
        sys.stdout.write(bcolors.ENDC)
        sys.exit(1)


# create the top-level parser
parser = argparse.ArgumentParser(
        prog=PROGNAME,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)


parser.add_argument('--config', '-c', 
                    default=os.path.join(SCRIPT_DIR, 'config.yaml'),
                    help='configuration file to load')

subparsers = parser.add_subparsers(help='sub-command help')


parser_push = subparsers.add_parser(
    'push', help='push monitors to datadog')
parser_push.set_defaults(command=command_push)


parser_diff = subparsers.add_parser(
    'diff',
    help='show diff between local monitors and datadog')
parser_diff.set_defaults(command=command_diff)


parser_mute = subparsers.add_parser(
    'mute',
    help='Mute business-hours-only alerts if it is not business hours')
parser_mute.set_defaults(command=command_mute)
args = parser.parse_args()


CONFIG = _load_config(args.config)


def main():
    datadog.initialize(**CONFIG['datadog'])
    args.command()


if __name__ == '__main__':
    main()
