const express = require('express');
const mysql2 = require('mysql2');

const app = express();
const PORT = 3000;

const db = mysql2.createConnection({
  host: '127.0.0.1',
  port: 3306,
  user: 'appuser',
  password: 'apppass',
  database: 'catalog'
});

db.connect((err) => {
  if (err) {
    console.error('Database connection failed:', err);
    process.exit(1);
  }
  console.log('Connected to MySQL database');
});

app.get('/', (req, res) => {
  res.send(`<!DOCTYPE html>
<html>
<head><title>Product Catalog</title></head>
<body>
<h1>Product Catalog</h1>
<p>Search for products by name.</p>
<form action="/search" method="get">
  <input type="text" name="search" placeholder="Enter product name..." />
  <button type="submit">Search</button>
</form>
</body>
</html>`);
});

app.get('/search', (req, res) => {
  const search = req.query.search || '';
  const sql = `SELECT name, description FROM products WHERE name LIKE '%${search}%'`;
  db.query(sql, (err, results) => {
    if (err) {
      return res.status(500).json({ error: err.message });
    }
    res.setHeader('Content-Type', 'application/json');
    res.json(results);
  });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Product catalog app listening on 0.0.0.0:${PORT}`);
});
