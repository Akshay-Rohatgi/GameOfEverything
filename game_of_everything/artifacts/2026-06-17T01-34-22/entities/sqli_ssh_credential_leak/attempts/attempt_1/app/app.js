const express = require('express');
const mysql2 = require('mysql2');

const app = express();
const PORT = 3000;

// Create MySQL connection pool
const pool = mysql2.createPool({
  host: '127.0.0.1',
  port: 3306,
  user: 'appuser',
  password: 'apppassword',
  database: 'catalog',
  waitForConnections: true,
  connectionLimit: 10,
  queueLimit: 0
});

const promisePool = pool.promise();

// Initialize database tables and seed data
async function initDb() {
  try {
    await promisePool.query(`
      CREATE TABLE IF NOT EXISTS products (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255),
        description VARCHAR(255)
      )
    `);

    await promisePool.query(`
      CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(64),
        password VARCHAR(128)
      )
    `);

    const [productRows] = await promisePool.query('SELECT COUNT(*) as cnt FROM products');
    if (productRows[0].cnt === 0) {
      await promisePool.query(
        'INSERT INTO products (name, description) VALUES (?, ?), (?, ?), (?, ?)',
        ['Widget A', 'A standard widget', 'Widget B', 'A premium widget', 'Gadget X', 'A useful gadget']
      );
    }

    const [userRows] = await promisePool.query('SELECT COUNT(*) as cnt FROM users');
    if (userRows[0].cnt === 0) {
      await promisePool.query(
        'INSERT INTO users (username, password) VALUES (?, ?)',
        ['sysadmin', 'Sup3rS3cr3tSSH!']
      );
    }

    console.log('Database initialized successfully');
  } catch (err) {
    console.error('Database initialization error:', err);
    setTimeout(initDb, 3000);
  }
}

// Landing page
app.get('/', (req, res) => {
  res.send(`
    <!DOCTYPE html>
    <html>
    <head><title>Product Catalog</title></head>
    <body>
      <h1>Product Catalog</h1>
      <form action="/search" method="GET">
        <input type="text" name="q" placeholder="Search products..." />
        <button type="submit">Search</button>
      </form>
    </body>
    </html>
  `);
});

// Vulnerable search endpoint - UNION-based SQL injection
app.get('/search', async (req, res) => {
  const q = req.query.q !== undefined ? req.query.q : '';
  // VULNERABLE: Direct string concatenation without sanitization
  const query = "SELECT name, description FROM products WHERE name LIKE '%" + q + "%'";
  try {
    const [rows] = await promisePool.query(query);
    res.json(rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, '0.0.0.0', async () => {
  console.log(`Server listening on 0.0.0.0:${PORT}`);
  await initDb();
});
