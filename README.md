# Opsdroid skill Hubspot

A skill for [opsdroid](https://github.com/opsdroid/opsdroid) to integrate with the Hubspot API.

This skill will get events from GitHub and do actions on HubSpot, for example, when a user creates a new issue, it will create a new ticket in
Hubspot, if we close an issue on Github, this skill will then close the ticket automatically.

It's recommended that you have an database setup with this skill so you can use [opsdroid memory](https://docs.opsdroid.dev/en/stable/skills/memory.html) to save information on the database which will prevent you from calling the API multiple times.


## Requirements

    - API Key optained from the settings (Account Setup > Integrations > API Key)

## Configuration

```yaml
skills:
  hubspot:
    token: <your hubspot token>

```

## Usage


## Development

How to set up skill, mention no cache, path.