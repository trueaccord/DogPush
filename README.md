# DogPush

DogPush enables to manage your DataDog monitors in YAML files.

## Why would I want ot manage my monitors in YAML files?

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

## Config file

DogPush config file defines  up the DataDog api and app keys. If they are not
specified in the file DogPush looks for these in environment variables named
`DATADOG_API_KEY` and `DATADOG_APP_KEY`.

The file defines the teams and how to alert your team at different severity
levels.

See `config-sample.yaml` for a full reference.

## Rule files

The config file references your rule files.  See `rds.yaml` for an example
rule file.

## Available commands

### dogpush diff

Prints the diff between the monitors in the files and the monitors in datadog.
This is useful when just starting out so you can build your initial rule
files.  Also, use `dogpush diff` before `dogpush push` as a dry-run to see
what will change.

### dogpush push

Pushes the local monitors to datadog:

- will create new monitors
- will update existing monitors (so it could override what you were doing if
  you edit an existing monitor in datadog)
- will *never* remove or touch untracked monitors (that is, datadog monitors
  that are not any of the yaml files).

This command can run from a cronjob to ensure the monitors on DataDog are
synchronized with the local monitors.

### dogpush mute

Use `dogpush mute` to mute monitors that have a `mute_when` tag.  This command
will mute these monitors, and the mute will automatically expires when the
period described by `mute_when` is over.  This command can be run
from a cron job to ensure monitors are silenced at the right times. As the
mute automatically expires, there is no need to run anything to unmute the
alerts.

