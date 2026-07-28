---
id: sensitive_file
description: Creates a file that contains sensitive information with weak permissions that can be exploited.
required_vars: [file_path, file_content]
---

# Atom: Sensitive File Creation
Creates a file with sensitive information and weak permissions to facilitate exploitation.

### Logic Requirements:
1. Create the target file with specified content: `echo "<file_content>" > <file_path>`
2. Set weak permissions on the file: `chmod 777 <file_path>`, `chown nobody:nogroup <file_path>`, or whatever is contextually appropriate.

### Common Patterns:
- **World Readable File:** `chmod 777 /tmp/sensitive_info.txt`
- **File owned by low-privilege user:** `chown operator:operator /home/operator/credentials.txt`

### Testing Guidance:
1. Verify that the file is created with the correct content: `cat <file_path>`
2. Check the permissions of the file: `ls -l <file_path>`
3. Attempt to access the file from a different user context to confirm that it is accessible due to the weak permissions. `printf <password>\n | su - otheruser -c "cat <file_path>"`


### Synthesis Guidance:
Generate the commands to create the file with the specified content and set weak permissions on it. Consider the context to determine the most appropriate weak permission settings. For example, if the context implies a public file, use `chmod 777`. If it implies a privilege escalation measure via a user the attacker can access, set the ownership accordingly (e.g. `chown operator:operator <file_path>`).