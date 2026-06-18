<?php
// Database setup using SQLite via PDO
define('DB_PATH', '/var/db/app.db');

function get_db() {
    $db = new PDO('sqlite:' . DB_PATH);
    $db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_SILENT);
    return $db;
}

function init_db() {
    $db = get_db();
    $db->exec("CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR(128),
        description VARCHAR(256)
    )");
    $db->exec("CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username VARCHAR(64),
        password VARCHAR(128),
        role VARCHAR(32)
    )");
    // Seed products if empty
    $count = $db->query("SELECT COUNT(*) FROM products")->fetchColumn();
    if ($count == 0) {
        $db->exec("INSERT INTO products (name, description) VALUES
            ('Widget A', 'A standard widget for everyday use'),
            ('Widget B', 'An improved widget with extra features'),
            ('Gadget Pro', 'Professional grade gadget'),
            ('Gadget Lite', 'Lightweight version of the gadget')");
    }
    // Seed users if empty
    $count = $db->query("SELECT COUNT(*) FROM users")->fetchColumn();
    if ($count == 0) {
        $db->exec("INSERT INTO users (username, password, role) VALUES
            ('sysadmin', 'Sup3rS3cr3tSSH!', 'admin'),
            ('deploy', 'deploy_pass_2024', 'user')");
    }
}

init_db();

$results = [];
$error = '';
$searched = false;

if (isset($_GET['search'])) {
    $searched = true;
    $search = $_GET['search'];
    $db = get_db();
    // VULNERABLE: direct string concatenation, no sanitization
    $query = "SELECT name, description FROM products WHERE name LIKE '%" . $search . "%'";
    $stmt = $db->query($query);
    if ($stmt !== false) {
        $results = $stmt->fetchAll(PDO::FETCH_NUM);
    } else {
        $info = $db->errorInfo();
        $error = isset($info[2]) ? $info[2] : 'Query error';
    }
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Product Catalog</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #f4f4f4; }
        h1 { color: #333; }
        form { margin-bottom: 20px; }
        input[type=text] { padding: 6px; width: 300px; font-size: 14px; }
        input[type=submit] { padding: 6px 16px; font-size: 14px; cursor: pointer; }
        table { border-collapse: collapse; width: 100%; background: #fff; }
        th { background: #333; color: #fff; padding: 10px; text-align: left; }
        td { border: 1px solid #ccc; padding: 8px; }
        tr:nth-child(even) { background: #f9f9f9; }
        .error { color: red; }
        .no-results { color: #666; font-style: italic; }
    </style>
</head>
<body>
    <h1>Product Catalog</h1>
    <form method="GET" action="/index.php">
        <input type="text" name="search" placeholder="Search products..." value="<?php echo isset($_GET['search']) ? htmlspecialchars($_GET['search'], ENT_QUOTES) : ''; ?>">
        <input type="submit" value="Search">
    </form>

    <?php if ($error): ?>
        <p class="error">Error: <?php echo htmlspecialchars($error); ?></p>
    <?php endif; ?>

    <?php if ($searched): ?>
        <table>
            <thead>
                <tr>
                    <th>Product Name</th>
                    <th>Description</th>
                </tr>
            </thead>
            <tbody>
                <?php if (count($results) > 0): ?>
                    <?php foreach ($results as $row): ?>
                        <tr>
                            <td><?php echo $row[0]; ?></td>
                            <td><?php echo $row[1]; ?></td>
                        </tr>
                    <?php endforeach; ?>
                <?php else: ?>
                    <tr><td colspan="2" class="no-results">No products found.</td></tr>
                <?php endif; ?>
            </tbody>
        </table>
    <?php else: ?>
        <p>Enter a search term above to find products.</p>
    <?php endif; ?>
</body>
</html>
