const express = require('express');
const Database = require('better-sqlite3');

const app = express();
const PORT = 3000;

// Initialize SQLite database
const db = new Database('/opt/webapp/catalog.db');

// Create tables and seed data
db.exec(`
  CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    description TEXT
  );
  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    password TEXT,
    role TEXT
  );
`);

const productCount = db.prepare('SELECT COUNT(*) as cnt FROM products').get();
if (productCount.cnt === 0) {
  db.prepare("INSERT INTO products (name, description) VALUES (?, ?)").run('Widget A', 'A basic widget for general use');
  db.prepare("INSERT INTO products (name, description) VALUES (?, ?)").run('Widget B', 'An advanced widget with extra features');
  db.prepare("INSERT INTO products (name, description) VALUES (?, ?)").run('Gadget X', 'A compact gadget for everyday tasks');
  db.prepare("INSERT INTO products (name, description) VALUES (?, ?)").run('Gadget Y', 'A premium gadget with extended warranty');
}

const userCount = db.prepare('SELECT COUNT(*) as cnt FROM users').get();
if (userCount.cnt === 0) {
  db.prepare("INSERT INTO users (username, password, role) VALUES (?, ?, ?)").run('sysadmin', 'Sup3rS3cur3SSH!', 'admin');
  db.prepare("INSERT INTO users (username, password, role) VALUES (?, ?, ?)").run('deploy', 'deploy_pass_2024', 'service');
}

console.log('Database initialized');

app.get('/', (req, res) => {
  res.send(`<!DOCTYPE html>
<html>
<head><title>Product Catalog</title></head>
<body>
<h1>Product Catalog</h1>
<p>Welcome to our product catalog. Use the search form below to find products.</p>
<form action="/search" method="GET">
  <input type="text" name="q" placeholder="Search products..." />
  <button type="submit">Search</button>
</form>
</body>
</html>`);
});

app.get('/search', (req, res) => {
  const q = req.query.q || '';
  const sql = "SELECT name, description FROM products WHERE name LIKE '%" + q + "%'";
  try {
    const results = db.prepare(sql).all();
    res.json(results);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/health', (req, res) => {
  res.json({ status: 'ok' });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Server running on 0.0.0.0:${PORT}`);
});
