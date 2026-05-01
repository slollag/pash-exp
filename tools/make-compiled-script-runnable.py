# the raw compiled scripts that pash outputs are not immediately runnable because
# they assume directories are created in /tmp. this adds mkdirs to the script that creates
# those directories in the script

import re
import sys
from pathlib import Path

if len(sys.argv) != 5:
    print(
        "Usage: python3 make-compiled-script-runnable.py "
        "<compiled_script> <output_script> <old_runtime_path> <new_runtime_path>",
        file=sys.stderr,
    )
    sys.exit(1)

compiled_script = Path(sys.argv[1])
output_script = Path(sys.argv[2])
old_runtime_path = sys.argv[3].rstrip("/")
new_runtime_path = sys.argv[4].rstrip("/")

script = compiled_script.read_text()

# Replace PaSh binaries path with a new path if needed
script = script.replace(old_runtime_path, new_runtime_path)

# Find all FIFO paths.
fifo_paths = re.findall(r'"/tmp/pash_[^"]*/#fifo\d+"', script)
fifo_paths = [p.strip('"') for p in fifo_paths]

# Extract unique directories containing FIFOs.
fifo_dirs = sorted({str(Path(p).parent) for p in fifo_paths})

mkdir_block = "\n".join(f'mkdir -p "{d}"' for d in fifo_dirs)

# Add mkdirs at the top
script = (
    "#!/bin/bash\n\n"
    + mkdir_block
    + "\n\n"
    + script
)

output_script.write_text(script)
output_script.chmod(0o755)

print(f"Wrote gem5-ready script to {output_script}")
print(f"Added {len(fifo_dirs)} FIFO directories")