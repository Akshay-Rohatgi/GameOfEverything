from flask import Flask, request, jsonify, redirect, make_response

app = Flask(__name__)

@app.post('/api/items')
def create_item():
    return jsonify(ok=True, received=request.get_json()), 201

@app.get('/login')
def login_form():
    return '''<!DOCTYPE html>
<html><head><title>Login</title></head><body>
<form id="login-form" method="POST" action="/login">
  <input id="username" name="username" type="text" />
  <input id="password" name="password" type="password" />
  <button type="submit">Login</button>
</form>
</body></html>'''

@app.post('/login')
def login():
    if request.form.get('username') == 'admin' and request.form.get('password') == 'admin123':
        resp = make_response(redirect('/dashboard'))
        resp.set_cookie('session_id', 'abc123secret')
        return resp
    return 'Invalid', 401

@app.get('/dashboard')
def dashboard():
    return '<html><body><div class="dashboard">Welcome</div></body></html>'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
