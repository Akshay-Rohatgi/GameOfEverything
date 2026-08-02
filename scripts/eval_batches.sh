#!/bin/bash
# Predefined test batches for common eval scenarios
#
# Usage:
#   ./scripts/eval_batches.sh <batch_name>
#
# Available batches:
#   confirmed    - Only confirmed-passing fixtures (fast)
#   sqli         - All SQL injection fixtures
#   cmdi         - All command injection fixtures
#   xss          - All XSS fixtures
#   core         - SQLi + Command Injection across all runtimes
#   all          - All available fixtures (slow)

set -e

BATCH="$1"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

if [ -z "$BATCH" ]; then
    echo "Usage: $0 <batch_name>"
    echo ""
    echo "Available batches:"
    echo "  confirmed    - Only confirmed-passing fixtures (fast)"
    echo "  sqli         - All SQL injection fixtures"
    echo "  cmdi         - All command injection fixtures"
    echo "  xss          - All XSS fixtures"
    echo "  core         - SQLi + CMDi across all runtimes"
    echo "  all          - All available fixtures (slow)"
    exit 1
fi

case "$BATCH" in
    confirmed)
        echo "Testing confirmed-passing fixtures..."
        FIXTURES="tests/fixtures/entities/sqli_express.yaml,tests/fixtures/entities/cmdi_flask.yaml,tests/fixtures/entities/sqli_php.yaml,tests/fixtures/entities/xss_stored_php.yaml,tests/fixtures/entities/xss_admin_bot_express.yaml"
        ;;

    sqli)
        echo "Testing all SQL injection fixtures..."
        FIXTURES="tests/fixtures/entities/sqli_express.yaml,tests/fixtures/entities/sqli_flask.yaml,tests/fixtures/entities/sqli_php.yaml"
        ;;

    cmdi)
        echo "Testing all command injection fixtures..."
        FIXTURES="tests/fixtures/entities/cmdi_express.yaml,tests/fixtures/entities/cmdi_flask.yaml,tests/fixtures/entities/cmdi_php.yaml"
        ;;

    xss)
        echo "Testing all XSS fixtures..."
        FIXTURES="tests/fixtures/entities/xss_express.yaml,tests/fixtures/entities/xss_stored_flask.yaml,tests/fixtures/entities/xss_stored_php.yaml,tests/fixtures/entities/xss_reflected_express.yaml,tests/fixtures/entities/xss_reflected_flask.yaml,tests/fixtures/entities/xss_reflected_php.yaml,tests/fixtures/entities/xss_admin_bot_express.yaml,tests/fixtures/entities/xss_admin_bot_flask.yaml,tests/fixtures/entities/xss_admin_bot_php.yaml"
        ;;

    core)
        echo "Testing core vulnerabilities (SQLi + CMDi) across all runtimes..."
        FIXTURES="tests/fixtures/entities/sqli_express.yaml,tests/fixtures/entities/sqli_flask.yaml,tests/fixtures/entities/sqli_php.yaml,tests/fixtures/entities/cmdi_express.yaml,tests/fixtures/entities/cmdi_flask.yaml,tests/fixtures/entities/cmdi_php.yaml"
        ;;

    all)
        echo "Testing ALL available fixtures (this will take a while)..."
        # Get all yaml files in the entities directory
        FIXTURES=$(ls tests/fixtures/entities/*.yaml | tr '\n' ',' | sed 's/,$//')
        ;;

    *)
        echo "Unknown batch: $BATCH"
        echo "Run without arguments to see available batches"
        exit 1
        ;;
esac

OUTPUT_DIR="eval_results/batch_${BATCH}_${TIMESTAMP}"

NUM_FIXTURES=$(echo $FIXTURES | tr ',' '\n' | wc -l)
echo ""
echo "╔════════════════════════════════════════╗"
echo "║     GoE v2 Batch Evaluation            ║"
echo "╠════════════════════════════════════════╣"
echo "║ Batch:    $BATCH"
echo "║ Entities: $NUM_FIXTURES"
echo "║ Output:   $OUTPUT_DIR"
echo "╚════════════════════════════════════════╝"
echo ""

# Run with progress bar (tqdm will show progress automatically)
.venv/bin/python -m goe.eval --suite build \
    --fixtures "$FIXTURES" \
    --output "$OUTPUT_DIR"

echo ""
echo "========================================="
echo "Batch test complete!"
echo "Results: $OUTPUT_DIR"
echo "========================================="
