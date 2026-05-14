---
id: toctou_race_condition
description: Creates a web endpoint vulnerable to a Time-of-Check to Time-of-Use (TOCTOU) race condition, allowing attackers to bypass limits or double-spend.
required_vars: [endpoint_path, target_action]
---

# Atom: TOCTOU Race Condition
Creates a vulnerable endpoint in a web application that checks a condition (like a balance or an item count), yields execution or delays slightly, and then performs an action based on the outdated check. This allows a race condition to exploit the application.

### Logic Requirements:
1. Define a web route at `<endpoint_path>` that receives a request to perform `<target_action>` (e.g., redeem a coupon, transfer funds).
2. The logic must read a shared state (e.g., a file or database row without a lock) to check if the action is allowed.
3. Introduce a small, unnatural delay (e.g., `time.sleep(0.5)`) or perform an async yield after the check but before the state is updated.
4. Update the state and perform the action, ignoring if another request modified the state during the delay.

### Common Patterns:
- **Python (Flask) Double Spend:**
  ```python
  import time
  from flask import Flask, request, jsonify

  app = Flask(__name__)
  # Vulnerable shared state
  user_balance = {"user1": 100}

  @app.route('/<endpoint_path>', methods=['POST'])
  def transfer():
      amount = int(request.form.get('amount', 0))
      
      # Time of Check
      if user_balance["user1"] >= amount:
          # Artificial delay to widen the race window
          time.sleep(0.5)
          
          # Time of Use
          user_balance["user1"] -= amount
          return jsonify({"status": "success", "remaining": user_balance["user1"]}), 200
      else:
          return jsonify({"status": "error", "message": "Insufficient funds"}), 400
  ```

### Testing Guidance:
1. Start the target web application.
2. Send multiple concurrent requests to the endpoint to exploit the race window.
   ```bash
   curl -X POST http://localhost/<endpoint_path> -d "amount=100" &
   curl -X POST http://localhost/<endpoint_path> -d "amount=100" &
   wait
   ```
3. Verify that the `<target_action>` occurred more times than theoretically allowed by the initial state (e.g., the balance is now negative, indicating a double spend).

### Synthesis Guidance:
Generate a vulnerable route that manages a shared resource. Ensure there is a distinct gap (using a sleep or heavy computation) between checking the validity of an action and committing the result. Do not implement locking mechanisms like database row-locks (`SELECT FOR UPDATE`) or threading locks, as these would remediate the vulnerability.