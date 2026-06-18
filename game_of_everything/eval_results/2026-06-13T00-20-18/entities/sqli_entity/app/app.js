const express = require('express');
const Database = require('better-sqlite3');
const path = require('path');

const app = express();
const PORT = 3000;
const DB_PATH = path.join(__dirname, 'db.sqlite');

// Initialize database
const db = new Database(DB_PATH);

db.exec(`
  CREATE TABLE IF NOT EXISTS items (
    name TEXT,
    desc TEXT
  );

  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    password TEXT
  );
`);

// Seed data if empty
const itemCount = db.prepare('SELECT COUNT(*) as cnt FROM items').get();
if (itemCount.cnt === 0) {
  db.prepare("INSERT INTO items (name, desc) VALUES ('Widget A', 'A standard widget')").run();
  db.prepare("INSERT INTO items (name, desc) VALUES ('Widget B', 'An improved widget')").run();
  db.prepare("INSERT INTO items (name, desc) VALUES ('Gadget X', 'A useful gadget')").run();
  db.prepare("INSERT INTO items (name, desc) VALUES ('Gadget Y', 'A premium gadget')").run();
}

const userCount = db.prepare('SELECT COUNT(*) as cnt FROM users').get();
if (userCount.cnt === 0) {
  db.prepare("INSERT INTO users (username, password) VALUES ('admin', 'supersecret123')").run();
  db.prepare("INSERT INTO users (username, password) VALUES ('alice', 'password1')").run();
}

// Routes
app.get('/', (req, res) => {
  res.send(`
    <!DOCTYPE html>
    <html>
    <head><title>Product Catalog</title></head>
    <body>
      <h1>Product Catalog</h1>
      <p>Search for products in our catalog.</p>
      <form action="/search" method="get">
        <input type="text" name="search" placeholder="Search products..." />
        <button type="submit">Search</button>
      </form>
    </body>
    </html>
  `);
});

app.get('/search', (req, res) => {
  const search = req.query.search || '';
  // Vulnerable: search parameter concatenated directly into SQL query
  const query = `SELECT name, desc FROM items WHERE name LIKE '%${search}%'`;
  try {
    const rows = db.prepare(query).all();
    res.json(rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Product catalog app listening on 0.0.0.0:${PORT}`);
});
