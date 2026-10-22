// Print placeholderRecord for each schema file named on the command line, as one JSON object keyed by
// file name. Used by test_placeholder_records.py, which checks every record against its schema with
// Python's jsonschema.
import { readFileSync } from "node:fs";
import { basename } from "node:path";

import { placeholderRecord } from "../../demo/web/core/schema.js";

const records = {};
for (const file of process.argv.slice(2)) {
  records[basename(file)] = placeholderRecord(JSON.parse(readFileSync(file, "utf8")));
}
process.stdout.write(JSON.stringify(records));
