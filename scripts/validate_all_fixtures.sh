#!/bin/bash
# Validate all fixtures and generate a comprehensive report
#
# Usage:
#   ./scripts/validate_all_fixtures.sh [--quick]
#
# Options:
#   --quick    Test only confirmed-passing fixtures (fast)
#
# Output:
#   eval_results/fixture_validation_<timestamp>/

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="eval_results/fixture_validation_${TIMESTAMP}"

# Confirmed passing fixtures
CONFIRMED=(
    "tests/fixtures/entities/sqli_express.yaml"
    "tests/fixtures/entities/cmdi_flask.yaml"
    "tests/fixtures/entities/sqli_php.yaml"
    "tests/fixtures/entities/xss_stored_php.yaml"
    "tests/fixtures/entities/xss_admin_bot_express.yaml"
)

# All available fixtures
ALL_FIXTURES=(tests/fixtures/entities/*.yaml)

if [ "$1" = "--quick" ]; then
    echo "Quick mode: Testing only confirmed-passing fixtures"
    FIXTURES=("${CONFIRMED[@]}")
else
    echo "Full mode: Testing all available fixtures"
    FIXTURES=("${ALL_FIXTURES[@]}")
fi

NUM_FIXTURES="${#FIXTURES[@]}"

echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║        GoE v2 Fixture Validation                  ║"
echo "╠═══════════════════════════════════════════════════╣"
if [ "$1" = "--quick" ]; then
    echo "║ Mode:     QUICK (confirmed fixtures only)         ║"
else
    echo "║ Mode:     FULL (all available fixtures)           ║"
fi
echo "║ Fixtures: $NUM_FIXTURES entities                        "
echo "║ Output:   $OUTPUT_DIR"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# Join array into comma-separated string
FIXTURES_STR=$(IFS=,; echo "${FIXTURES[*]}")

echo "Starting evaluation..."
echo ""

# Run eval (tqdm will show progress)
.venv/bin/python -m goe.eval --suite build \
    --fixtures "$FIXTURES_STR" \
    --output "$OUTPUT_DIR"

echo ""
echo "========================================="
echo "Validation complete!"
echo "Results: $OUTPUT_DIR"
echo "========================================="
echo ""

# Parse results
SUMMARY_FILE="$OUTPUT_DIR/$(ls -t $OUTPUT_DIR/*.json 2>/dev/null | head -1 | xargs basename 2>/dev/null || echo 'summary.json')"

if [ -f "$OUTPUT_DIR/summary.json" ]; then
    echo "Quick summary:"
    python3 <<EOF
import json
with open('$OUTPUT_DIR/summary.json') as f:
    data = json.load(f)
    print(f"  Entities tested: {data['entities_tested']}")
    print(f"  Passed: {data['entities_passed']} ({100*data['entities_passed']/data['entities_tested']:.1f}%)")
    print(f"  Failed: {data['entities_failed']}")
    print(f"  Mean attempts: {data['mean_attempts']:.2f}")
    print(f"  Total tokens: {data['total_tokens']:,}")
    print(f"  Total time: {data['total_latency_ms']/1000:.1f}s")

    if data['entity_details']:
        print(f"\\nPer-entity results:")
        for e in data['entity_details']:
            status_icon = '✓' if e['status'] == 'PASSED' else '✗'
            print(f"    {status_icon} {e['id']}: {e['runtime']} - {', '.join(e['atoms'])}")
EOF
fi

echo ""
echo "To see full details:"
echo "  cat $OUTPUT_DIR/summary.json | jq"
