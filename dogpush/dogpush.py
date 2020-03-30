#!/usr/bin/env python

import argparse
import calendar
import copy
import datetime
import difflib
import glob
import os
import re
import sys

import datadog
import datadog.api
import pytz
import yaml

import bcolors


PROGNAME = 'dogpush'


class DogPushException(Exception):
    pass


def _load_config(config_file):
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
    if 'teams' not in config:
        config['teams'] = {}
    if 'datadog' not in config:
        config['datadog'] = {}
    config['datadog']['app_key'] = config['datadog'].get(
            'app_key', os.getenv('DATADOG_APP_KEY'))
    config['datadog']['api_key'] = config['datadog'].get(
            'api_key', os.getenv('DATADOG_API_KEY'))
    config['datadog']['mute'] = config['datadog'].get('mute', False)
    config['default_rule_options'] = config.get(
        'default_rule_options', LOCAL_DEFAULT_RULE_OPTIONS)
    config['default_rules'] = config.get(
        'default_rules', DATADOG_DEFAULT_RULES)
    if 'dogpush' not in config:
        config['dogpush'] = {}
    config['dogpush']['ignore_prefix'] = config['dogpush'].get(
        'ignore_prefix', None)
    config['dogpush']['yaml_width'] = config['dogpush'].get('yaml_width', None)
    # Ensure the keys of the above two groups are disjoint.
    assert(set(config['default_rule_options'].keys()) &
           set(DATADOG_DEFAULT_OPTIONS.keys()) == set())

    return config


SEVERITIES = ['CRITICAL', 'WARNING', 'INFO']


SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))


# Datadog fields we do not store locally.
IGNORE_FIELDS = ['created_at', 'created', 'modified', 'creator',
                 'org_id', 'overall_state', 'id', 'deleted',
                 'matching_downtimes', 'overall_state_modified',
                 # dogpush specific:
                 'mute_when', 'team', 'severity']

IGNORE_OPTIONS = ['silenced']

# Datadog fields that we do not store in our monitor rules if they have the
# default value.
# There are two type of defaults: defaults coming from TrueAccord (all
# our rules get these values by default) and defaults coming from DataDog.
LOCAL_DEFAULT_RULE_OPTIONS = {
  'notify_no_data': True,
  'renotify_interval': 15,
}

DATADOG_DEFAULT_OPTIONS = {
  'notify_audit': False,
  'locked': False,
  'silenced': {}
}

DATADOG_DEFAULT_RULES = {
  'multi': False,
  'type': 'metric alert'
}

def _pretty_yaml(d):
    return re.sub('^-', '\n-',
                  yaml.dump(d, width=CONFIG['dogpush']['yaml_width']),
                  flags=re.M)


# Transform a monitor to a canonical form by removing defaults
def _canonical_monitor(original, default_team=None, **kwargs):
    m = copy.deepcopy(original)
    if 'tags' in m and not m['tags']:
        del m['tags']
    for field in IGNORE_FIELDS:
        m.pop(field, None)
    for field in IGNORE_OPTIONS:
        m.get('options', {}).pop(field, None)

    # Default options from env var overwrite default options in config file
    option_defaults = dict({})
    option_defaults.update(CONFIG['default_rule_options'])
    option_defaults.update(DATADOG_DEFAULT_OPTIONS)

    # print(option_defaults)

    for (field, value) in option_defaults.items():
        if value is m.get('options',{}).get(field):
            del m.get('options',{})[field]

    # Rules in monitor config overwrite default rules
    for (field, value) in CONFIG['default_rules'].items():
        if value is m.get('options',{}).get(field):
            del m.get('options',{})[field]

    # If options is {'thresholds': {'critical': x}}, then it is redundant.
    if not m.get('options'):
        m.pop('options', None)
    elif m['options'].keys() == ['thresholds'] and m['options']['thresholds'].keys() == ['critical']:
        del m['options']
    m['name'] = m['name'].strip()
    original_team = original.get('team')
    team = original_team if original_team is not None else default_team
    severity = original.get('severity') or 'CRITICAL'
    if team:
        if isinstance(team, str):
            team = [team]
        m['message'] = m.get('message', '')
        for t in team:
            dogpush_line=""
            notifications_rcpt = CONFIG['teams'][t]['notifications'][severity]
            if severity == 'WARNING':
                dogpush_line = "{{#is_warning}}"+notifications_rcpt+"{{/is_warning}}{{#is_warning_recovery}}"+notifications_rcpt+"{{/is_warning_recovery}}"
            elif severity == 'CRITICAL':
                dogpush_line = "{{#is_alert}}"+notifications_rcpt+"{{/is_alert}}{{#is_recovery}}"+notifications_rcpt+"{{/is_recovery}}"
            else:
                dogpush_line = notifications_rcpt
            m['message'] += ('\n' if m['message'] else '') + dogpush_line

    result = dict(
        name = m['name'],
        id = original.get('id'),
        obj = m,
        mute_when = original.get('mute_when'),
        is_silenced = bool(original.get('options', {}).get('silenced'))
    )
    result.update(kwargs)
    return result


def get_datadog_monitors():
    monitors = datadog.api.Monitor.get_all(with_downtimes="true")
    if CONFIG['dogpush']['ignore_prefix'] is not None:
        monitors = [
            m for m in monitors
            if not m['name'].startswith(CONFIG['dogpush']['ignore_prefix'])
        ]

    if not _check_monitor_names_unique(monitors):
        raise DogPushException(
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
                print("Duplicate name: {}".format(name),file=sys.stderr)
        return False
    return True


def _check_monitor(monitor, location):
    name = monitor.get('name', '')
    if isinstance(name, str):
        name = name.strip()
    if not name:
        raise DogPushException(
            '{}: found monitor without a name'.format(location)
        )


def get_local_monitors():
    monitors = []
    for globname in CONFIG.get('rule_files', []):
        if not os.path.isabs(globname):
            globname = os.path.join(CONFIG_DIR, globname)
        for filename in glob.glob(globname):
            with open(filename, 'r') as f:
                r = yaml.safe_load(f)
                if r is None:
                    r = {'alerts': []}
                if not isinstance(r, dict):
                    raise DogPushException("Expected a dictionary")
                if not isinstance(r.get('alerts'), list):
                    raise DogPushException("'alerts' must be a list of alerts.")
                default_team = r.get('team')
                for monitor in r['alerts']:
                    _check_monitor(monitor, filename)
                    monitor = _canonical_monitor(monitor,
                                                 filename=filename,
                                                 default_team=default_team)
                    monitors.append(monitor)
    if not _check_monitor_names_unique(monitors):
        raise DogPushException('Duplicate names found in local monitors.')
    result = dict((m['name'], m) for m in monitors)
    return result


def _prepare_monitor(m):
    obj = copy.deepcopy(m['obj'])
    for (key, value) in CONFIG['default_rule_options'].items():
        obj['options'] = obj.get('options', {})
        if key not in obj['options']:
            obj['options'][key] = value
    for (key, value) in CONFIG['default_rules'].items():
        obj[key] = obj.get(key, value)
    return obj


def _is_changed(local, remote):
    # For an alert with `mute_when`, we ignore silencing when comparing.
    # TODO(nadavsr): rethink how silencing should affect monitors in general.
    if local['mute_when']:
        remote['obj'].get('options', {}).pop('silenced', None)

    return local['obj'] != remote['obj']


def command_init(args):
    remote_monitors = [m['obj'] for m in get_datadog_monitors().values()]
    monitors = {'alerts': remote_monitors}
    print('# team: TEAMNAME')
    print
    print(_pretty_yaml(monitors))


def command_push(args):
    local_monitors = get_local_monitors()
    remote_monitors = get_datadog_monitors()

    only_local = set(local_monitors.keys()) - set(remote_monitors.keys())
    if only_local:
        print("Pushing {} new monitors.".format(len(only_local)))
        for name in only_local:
            datadog.api.Monitor.create(**_prepare_monitor(local_monitors[name]))

    common_names = set(local_monitors.keys()) & set(remote_monitors.keys())
    changed = [name for name in common_names
               if _is_changed(local_monitors[name], remote_monitors[name])]
    if changed:
        print("Updating {} modified monitors.".format(len(changed)))
        for name in changed:
            datadog.api.Monitor.update(
                remote_monitors[name]['id'],
                **_prepare_monitor(local_monitors[name]))

    if args.delete_untracked:
        remote_monitors = get_datadog_monitors()
        untracked = set(remote_monitors.keys()) - set(local_monitors.keys())
        if untracked:
            print("Deleting {} untracked monitors.".format(len(untracked)))
            for monitor in untracked:
                datadog.api.Monitor.delete(remote_monitors[monitor]['id'])


def _should_mute(expr, tz, now):
    return eval(expr, {}, {'now': now.astimezone(tz)})


def _mute_until(expr, tz, now):
    """Returns the earliest time the given expression returns false."""
    # Adds one hour until `expr` return true.  This is
    # actually pretty fast (less than 1ms), and provides a good way to avoid
    # avoids a more complex calcuation.
    start = now
    one_hour = datetime.timedelta(hours=1)
    while _should_mute(expr, tz, now=now):
        now += one_hour
    # Round to the start of the hour.
    if start != now:
        now -= datetime.timedelta(minutes=now.minute,
                                  seconds=now.second,
                                  microseconds=now.microsecond)
    return now


def command_mute(args):
    local_monitors = get_local_monitors()
    remote_monitors = get_datadog_monitors()
    mute_tags = {}

    now = datetime.datetime.now(pytz.UTC)
    for tag_key, tag_value in CONFIG.get('mute_tags', {}).items():
        tz = pytz.timezone(tag_value['timezone'])
        if _should_mute(tag_value['expr'], tz, now):
            next_active_time = _mute_until(tag_value['expr'], tz, now)
            mute_tags[tag_key] = {
                'datetime': next_active_time.astimezone(tz),
                'timestamp': calendar.timegm(next_active_time.timetuple())
            }
        else:
            mute_tags[tag_key] = None

    for monitor in local_monitors.values():
        if monitor['mute_when'] and remote_monitors.has_key(monitor['name']):
            remote = remote_monitors[monitor['name']]
            if remote['is_silenced']:
                print("Alert '{}' is already muted. Skipping.".format(monitor['name']))
                continue
            mute_until = mute_tags[monitor['mute_when']]
            if mute_until:
                id = remote['id']
                datadog.api.Monitor.mute(id, end=mute_until['timestamp'])
                print(
                    "Muting alert '{}' until {}".format(
                        monitor['name'],
                        mute_until['datetime']
                    )
                )

def command_diff(args):
    local_monitors = get_local_monitors()
    remote_monitors = get_datadog_monitors()

    only_local = set(local_monitors.keys()) - set(remote_monitors.keys())
    only_remote = set(remote_monitors.keys()) - set(local_monitors.keys())
    common_names = set(local_monitors.keys()) & set(remote_monitors.keys())
    changed = [name for name in common_names
               if _is_changed(local_monitors[name],
                  remote_monitors[name])]

    if only_local:
        sys.stdout.write(bcolors.WARN)
        print('---------------------------------------------------------')
        print(' NEW MONITORS.  These monitors are currently missing in')
        print(' datadog and can be pushed using "{} push"'.format(PROGNAME))
        print('---------------------------------------------------------')
        sys.stdout.write(bcolors.ENDC)
        monitors = [local_monitors[name]['obj'] for name in only_local]
        print(_pretty_yaml(monitors))
    if changed:
        sys.stdout.write(bcolors.WARN)
        print('---------------------------------------------------------')
        print(' TO BE UPDATED.  These monitors exist in datadog, but are')
        print(' different than the local version.  Use "{} push"'.format(
            PROGNAME
        ))
        print(' to push them to datadog.')
        print('---------------------------------------------------------')
        print
        sys.stdout.write(bcolors.ENDC)
        for name in changed:
            remote_name = 'datadog:{name}'.format(name=name)
            local_name = '{filename}:{monitor_name}'.format(
              filename=local_monitors[name]['filename'],
              monitor_name=name)
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
    if only_remote and not args.ignore_untracked:
        sys.stdout.write(bcolors.WARN)
        print('------------------------------------------------------------')
        print(' UNTRACKED MONITORS.  These monitors are only in datadog    ')
        print(' and needed to be MANUALLY added to a local file or removed ')
        print(' from datadog.                                              ')
        print('------------------------------------------------------------')
        sys.stdout.write(bcolors.ENDC)
        monitors = [remote_monitors[name]['obj'] for name in only_remote]
        print(_pretty_yaml(monitors))
        sys.stdout.write(bcolors.FAIL)
        print("*** FAILED *** Untracked monitors found.")
        sys.stdout.write(bcolors.ENDC)
    if args.exit_status and any((only_local, changed, only_remote and not args.ignore_untracked)):
        sys.exit(1)


# create the top-level parser
parser = argparse.ArgumentParser(
        prog=PROGNAME,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)


parser.add_argument('-c', '--config',
                    default=os.path.join('.', 'config.yaml'),
                    help='configuration file to load')

subparsers = parser.add_subparsers(help='sub-command help')


parser_push = subparsers.add_parser(
    'init', help='Init new alerts file')
parser_push.set_defaults(command=command_init)


parser_push = subparsers.add_parser(
    'push', help='Push monitors to DataDog.')
parser_push.add_argument('-d', '--delete_untracked', action='store_true',
                         help='Delete untracked monitors.')
parser_push.set_defaults(command=command_push)


parser_diff = subparsers.add_parser(
    'diff',
    help='Show diff between local monitors and DataDog')
parser_diff.add_argument('-i', '--ignore_untracked', action='store_true',
                         help='Ignore untracked monitors.')
parser_diff.add_argument(
    '--no_exitstatus',
    dest='exit_status',
    action='store_false',
    default=True,
    help='Diff will return 0 if there are differences. Default (true), return 1 for differences.')
parser_diff.set_defaults(command=command_diff)


parser_mute = subparsers.add_parser(
    'mute',
    help='Mute alerts based on their `mute_when` key')
parser_mute.set_defaults(command=command_mute)
args = parser.parse_args()


CONFIG = _load_config(args.config)
CONFIG_DIR = os.path.abspath(os.path.dirname(args.config))


def main():
    datadog.initialize(**CONFIG['datadog'])
    args.command(args)


if __name__ == '__main__':
    main()
