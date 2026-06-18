const express = require('express');
const Database = require('better-sqlite3');

const app = express();
const PORT = 3000;

// Initialize SQLite database in memory
const db = new Database(':memory:');

// Create tables
db.exec(`
  CREATE TABLE IF NOT EXISTS products (
    name TEXT,
    description TEXT
  );
  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    password TEXT
  );
`);

// Seed data
db.exec(`
  INSERT INTO products (name, description) VALUES
    ('Widget A', 'A standard widget'),
    ('Widget B', 'An advanced widget'),
    ('Gadget X', 'A useful gadget');
  INSERT INTO users (username, password) VALUES
    ('admin', 'supersecret123');
`);

// Home route
app.get('/', (req, res) => {
  res.send(`
    <!DOCTYPE html>
    <html>
    <head><title>Product Catalog</title></head>
    <body>
      <h1>Product Catalog</h1>
      <p>Search for products by name:</p>
      <form action="/search" method="GET">
        <input type="text" name="search" placeholder="Enter product name" />
        <button type="submit">Search</button>
      </form>
    </body>
    </html>
  `);
});

// Vulnerable search route
app.get('/search', (req, res) => {
  const search = req.query.search || '';
  // VULNERABLE: unsanitized input concatenated directly into SQL
  const query = `SELECT name, description FROM products WHERE name LIKE '%${search}%'`;
  try {
    const rows = db.prepare(query).all();
    res.json(rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Server running on http://0.0.0.0:${PORT}`);
});
