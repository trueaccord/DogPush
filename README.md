# DogPush

DogPush enables to manage your DataDog monitors in YAML files.

## Why would I want to manage my monitors in YAML files?

Excellent question. So many reasons:

* You can store your monitors in source control so you can track changes (who
  changed this monitor?), and perform code reviews.
* Automatically includes the right @-mentions in the message based on team
  configurations.
* It is very easy to accidentally make changes to monitors on
  DataDog's UI.  By having your monitors documented in your own source code
  repository, you can set up a cron job that **pushes** the monitors to DataDog.
  This allows you to ensure that accidental changes get automatically
  corrected.
* DataDog has a missing feature: it is impossible to set up a monitor that
  is only active during business hours.  With DogPush it is really easy using
  `mute_tags`.

## Installation

## Via PIP

Run `pip install dogpush`

## Via Docker

Run `docker pull trueaccord/dogpush`

To run, you need ot make your configuration files accessible to the container.
Use a command like this:

```
docker run --rm -v /path/to/config:/config trueaccord/dogpush -c /config/config.yaml diff
```

## Getting Started

Go to [DataDog API settings](https://app.datadoghq.com/account/settings#api)
and generate an API key and application key.  Create a minimal config.yaml
that looks like this:

```yaml
---
datadog:
  api_key: YOUR_API_KEY
  app_key: YOUR_APP_KEY
```

Then run `dogpush -c ./config.yaml diff` and all the alerts you currently
have on datadog will appear as untracked.

The next step would be to create your initial alerts file:

```
dogpush -c ./config.yaml init > ./my_monitors.yaml
```

Now, add `my_monitors.yaml` as a rules_file to `config.yaml`. Edit
`config.yaml` again:

```yaml
---
datadog:
  api_key: YOUR_API_KEY
  app_key: YOUR_APP_KEY

rule_files:
- my_monitors.yaml
```

Now, run `dogpush diff` again, and see that the difference will be empty. Your
local rules are in sync with your DataDog rules.  If you have a lot of rules,
You may split your initial rules file to multiple files (by category, or
team), and include all of them in the `rule_files` section.  Paths can be
either relative to the config file or absolute. Paths may contain wildcards.

```yaml
rule_files:
- rds.yaml
- ec2.yaml
- dir1/rules.yaml
- /absolute/path/to/rules.yaml
- path/with/wildcard/*.yaml
```

Now you can make changes to your rule files. See the changes by running
`dogpush diff`, and push them using `dogpush push`.

## Config file

DogPush config file defines up the DataDog api and app keys. If they are not
specified in the file DogPush looks for these in environment variables named
`DATADOG_API_KEY` and `DATADOG_APP_KEY`.

The file defines the teams and how to alert your team at different severity
levels.

### Rule Defaults

Your config can optionally define two values to specify defaults.
`default_rules` can be used to specify defaults for the top level values of
rules, such as `multi` and `message`. `default_rule_options` can be used to
specify default values for the `options` section of rules, such as `locked` or
`notify_audit`. All of these values are automatically filled in for every rule
you define, but can always be overridden per-rule. For example,

```yaml
default_rules:
  multi: False
default_rule_options:
  notify_audit: True
  locked: True
```

### The teams section

Teams in DogPush are a way to append notification methods in a conditional fashion
to so it will grab the attention of the right people. By defining your
teams in the global config, it is super easy to add these @-mentions to all
your monitors. For example,
```yaml
teams:
  eng:
    notifications:
      alert: '@hipchat-Engineering @victorops-eng'
      warning: '@eng-alerts@example.com'
  ops:
    notifications:
      alert: '@hipchat-Ops'
```

means that when an monitor metric reaches the alert threshold the `eng` team
will be notfied via '@hipchat-Engineering @victorops-eng'.

Then, in a rules file, you can have a top level `team` setting (making all
the alerts automatically go to that team), or specify 'team' at the alert
level.

### Muting alerts based on time windows

DogPush supports automatically muting alerts. A common use case is to mute
non critical alerts outside business hours. First, define the time window like
this:
```yaml
mute_tags:
  not_business_hours:
    timezone: US/Pacific
    expr: now.hour < 9 or now.hour >= 17 or (now.weekday() in (5, 6))
```

This defines `not_business_hours` period. The period is defined using a
`timezone` and a Python expression.  The expression provides a `now` variable
of type [datetime.datetime](https://docs.python.org/2/library/datetime.html#datetime.datetime)
and should return True when `now` falls in a time where the monitor should be
muted.  In this example, not_business_hours is defined as before 9am or after
5pm or anytime during the weekend. The `timezone` key specifies in which
timezone should the `now` be localized to.  (all the internal time
calculations in DogPush are done in UTC regardless of the system's default
timezone).

After defining `mute_tags`, you can apply `mute_when: not_business_hours` to
rules in your rules file.  Also read about `dogpush mute` to learn how to
automatically mute these alerts.

See `config-sample.yaml` for a full example.

### DogPush options

There are two optional settings that can be configured in the config file:

```yaml
dogpush:
  yaml_width: 80
  ignore_prefix: 'string'
```

The `yaml_width` option sets the line width of the generated yaml output.

Using `ignore_prefix` one can define a set of monitor names that are
simply ignored by DogPush when fetching the remote monitors.

## Rule files

On the top of a rules file you can define `team: xyz` to define the default
team for all the alerts in the file. You can override the team by specifying a
different team in an alert.

**Tip 1:** it is possible to use a list of teams instead of a single team.

**Tip 2:** if you would like to have a monitor without any team associated
with it, you can use `team: []` in that monitor. This will override the file's
default team settings with an empty list.

The config file references your rule files.  See `rds.yaml` for an example
rule file.

## Available commands

### dogpush init

Prints an initial rules file so you can get started quickly with your existing
rules.

### dogpush diff

Prints the diff between the monitors in the files and the monitors in datadog.
This is useful when just starting out so you can build your initial rule
files.  Also, use `dogpush diff` before `dogpush push` as a dry-run to see
what will change.

`dogpush diff --no_exitstatus` will cause dogpush diff to return exit code 0
regardless of if there is a difference detected. If this flag is not present
then dogpush will return 1 if there is a difference between local and remote.

### dogpush push

Pushes the local monitors to datadog:

- will create new monitors
- will update existing monitors (so it could override what you were doing if
  you edit an existing monitor in datadog)
- will not remove or touch untracked monitors (that is, datadog monitors
  that are not in any of the yaml files) unless the `--delete_untracked` flag
  is passed in.

This command can run from a cronjob to ensure the monitors on DataDog are
synchronized with the local monitors.

### dogpush mute

Use `dogpush mute` to mute monitors that have a `mute_when` tag.  This command
will mute these monitors, and the mute will automatically expires when the
period described by `mute_when` is over.  This command can be run
from a cron job to ensure monitors are silenced at the right times. As the
mute automatically expires, there is no need to run anything to unmute the
alerts.
