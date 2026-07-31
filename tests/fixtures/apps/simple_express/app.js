const express = require('express');
const app = express();
app.use(express.json());

// POST /api/items — returns 201 + echoes body
app.post('/api/items', (req, res) => {
    res.status(201).json({ ok: true, received: req.body });
});

// GET /login — login form
app.get('/login', (req, res) => {
    res.send(`<!DOCTYPE html>
<html><head><title>Login</title></head><body>
<form id="login-form" method="POST" action="/login">
  <input id="username" name="username" type="text" placeholder="Username" />
  <input id="password" name="password" type="password" placeholder="Password" />
  <button type="submit" id="submit-btn">Login</button>
</form>
</body></html>`);
});

// POST /login — accepts admin/admin123, redirects to /dashboard
app.post('/login', express.urlencoded({ extended: true }), (req, res) => {
    if (req.body.username === 'admin' && req.body.password === 'admin123') {
        res.cookie('session_id', 'abc123secret');
        res.redirect('/dashboard');
    } else {
        res.status(401).send('Invalid credentials');
    }
});

// GET /dashboard
app.get('/dashboard', (req, res) => {
    res.send('<html><body><div class="dashboard">Welcome to the dashboard</div></body></html>');
});

// GET /forum — posts list with XSS bait
app.get('/forum', (req, res) => {
    res.send(`<!DOCTYPE html>
<html><head><title>Forum</title></head><body>
<div id="posts">
  <div class="post">
    <a href="/forum/1">Hello World Post</a>
  </div>
</div>
</body></html>`);
});

// POST /forum/posts — create post (stores raw HTML — intentionally vulnerable)
const posts = [];
app.post('/forum/posts', (req, res) => {
    const post = { id: posts.length + 1, content: req.body.content || '' };
    posts.push(post);
    res.status(201).json({ id: post.id });
});

// GET /forum/:id — view post (renders raw content — XSS)
app.get('/forum/:id', (req, res) => {
    const id = parseInt(req.params.id);
    const post = posts.find(p => p.id === id);
    if (!post) return res.status(404).send('Not found');
    res.send(`<html><body><div id="post-content">${post.content}</div></body></html>`);
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Listening on ${PORT}`));
