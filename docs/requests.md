# Sample Requests:

Sample Request 1:
```
Spin up an Ubuntu 22.04 server. It needs an anonymous Samba share containing a backup zip file with a user's username and password. Once I get SSH access, I want to be able to escalate privileges to root by exploiting a misconfigured SUID binary that allows the user to execute /bin/bash with root privileges.
```

In this case the request should be split up accordingly:
```
Context: Spin up an Ubuntu 22.04 server.
Initial Access Vector(s): It needs an anonymous Samba share containing a backup zip file with a user's username and password.
Privilege Escalation Vector(s): Once I get SSH access, I want to be able to escalate privileges to root by exploiting a misconfigured SUID binary that allows the user to execute /bin/bash with root privileges.
```
