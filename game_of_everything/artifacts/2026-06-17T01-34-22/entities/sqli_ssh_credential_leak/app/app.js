const express = require('express');
const mysql = require('mysql2');

const app = express();
const PORT = 3000;

const db = mysql.createConnection({
  host: '127.0.0.1',
  user: 'appuser',
  password: 'apppassword',
  database: 'appdb'
});

db.connect((err) => {
  if (err) {
    console.error('Database connection failed:', err);
    process.exit(1);
  }
  console.log('Connected to MySQL database');
});

app.get('/', (req, res) => {
  res.send('<!DOCTYPE html><html><head><title>Product Catalog</title></head><body><h1>Product Catalog</h1><form method="GET" action="/search"><input type="text" name="q" placeholder="Search products..." /><button type="submit">Search</button></form><h2>Featured Products</h2><ul><li><strong>Widget Alpha</strong> - A sturdy aluminum widget</li><li><strong>Widget Beta</strong> - A lightweight plastic widget</li><li><strong>Gadget Pro</strong> - Professional grade gadget</li><li><strong>Gadget Lite</strong> - Entry level gadget</li></ul></body></html>');
});

app.get('/search', (req, res) => {
  const q = req.query.q || '';
  const sql = "SELECT name, description FROM products WHERE name LIKE '%" + q + "%'";
  db.query(sql, (err, rows) => {
    if (err) {
      return res.status(500).json({ error: err.message });
    }
    res.json(rows);
  });
});

app.get('/health', (req, res) => {
  res.json({ status: 'ok' });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log('Server running on http://0.0.0.0:' + PORT);
});
