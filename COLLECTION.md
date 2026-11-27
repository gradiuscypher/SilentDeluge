# Baseline collection

```sh
python3 collection.py SOURCE --config PATH
```

Use Python 3.11+ and a JSON configuration object with a nonblank `scope`
string identifying the exact collection boundary (for example, an Incus
server/project, a UniFi controller/site, or a Linux asset). Other keys belong to
the selected provider. The runner passes it the object unchanged and takes no
declarations. `SOURCE` is `incus`, `unifi`, or `linux`.

The runner writes one JSON envelope to stdout: `schema_version` (`1`),
`source` (the CLI name), `scope` (from config), `timestamp` (the runner's UTC
collection time), and `sections`. Collection produces evidence, not a comparison
or a persisted inventory. Keep real configuration and evidence out of Git.

## Adding a provider

1. Add a standard-library module named `source_<name>.py` and register its CLI
   name and module name in the fixed `_PROVIDERS` mapping in `collection.py`.
   Configuration cannot select a module or filesystem import path.
2. Implement `collect(config)` returning a list of dictionaries. Each has a
   `section` name, an `outcome` (`complete`, `partial`, or `failed`), and a
   `records` list of JSON objects with source-local fields. Stored evidence may
   also include a section-level `timestamp` with an explicit UTC offset. Use
   that evidence's original capture time, not the replay time; the runner
   preserves it and dates the outer envelope separately.
3. Document the provider's configuration, scope, and allowed reads. Use only
   explicitly configured, read-only access. Keep credentials out of evidence
   and prefer references instead of credential values in config. Providers
   must not print or log configuration, credentials, or raw error bodies.
4. Exercise the provider with synthetic inputs, including incomplete coverage.

`complete` asserts successful coverage of the entire population the section
represents within its scope. Only that outcome permits using omitted records as
evidence of absence, and only for that population and scope. Use `partial` for
incomplete coverage with usable evidence and `failed` when the section could
not be collected. Partial or failed sections never establish absence or erase
prior verified evidence. An empty `sections` list supplies no evidence.

Exit status `0` means the runner emitted a valid envelope, not that every
section was complete: read the outcomes. A CLI/configuration error, an import
or provider exception, or invalid provider output exits with status `2`, emits
no envelope, and reports a fixed diagnostic on stderr without input values or
exception details.
