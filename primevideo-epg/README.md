# Prime Video US EPG for TiviMate

This project builds a supplemental XMLTV guide for the channels in the `PRIME:` group of the IPTV channel list supplied by the repository owner.

## TiviMate EPG URL

Use the raw GitHub URL:

`https://raw.githubusercontent.com/theuauchessclub/uau-backend/main/primevideo-epg/primevideo-us.xml`

Compressed version:

`https://raw.githubusercontent.com/theuauchessclub/uau-backend/main/primevideo-epg/primevideo-us.xml.gz`

## What it does

- Keeps the IPTV provider's exact `PRIME:` channel names in the generated XMLTV file.
- Matches those names to current EPG IDs from EPGShare sources.
- Prioritizes US national channels, US locals, US sports, Plex and Peacock guide sources.
- Uses conservative matching so an incorrect schedule is less likely to be assigned.
- Refreshes automatically every 6 hours using GitHub Actions.
- Produces `match-report.csv`, `unmatched.csv`, and `summary.json` after each refresh.

## Important

This is a community-built supplemental guide. It is **not an official Amazon/Prime Video XMLTV endpoint**. Amazon documents that its linear/live channels use program metadata and broadcast schedules for the Prime Video EPG, but Amazon does not publish a consumer XMLTV URL for TiviMate.

The channel files contain the 1,032 channels whose names begin with `PRIME:` in the supplied IPTV channel database. No IPTV username or password is stored in this repository.
