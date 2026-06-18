<?php
// Database setup - initialize SQLite DB with products and users tables
$db_path = '/var/db/app.db';

try {
    $pdo = new PDO('sqlite:' . $db_path);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

    // Create tables if they don't exist
    $pdo->exec("CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR(255),
        description VARCHAR(512)
    )");

    $pdo->exec("CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username VARCHAR(255),
        password VARCHAR(255)
    )");

    // Seed products if empty
    $count = $pdo->query("SELECT COUNT(*) FROM products")->fetchColumn();
    if ($count == 0) {
        $pdo->exec("INSERT INTO products (name, description) VALUES
            ('Widget Alpha', 'A high-quality aluminum widget'),
            ('Widget Beta', 'A durable plastic widget'),
            ('Gadget Pro', 'Professional grade gadget'),
            ('Gadget Lite', 'Entry-level gadget for home use'),
            ('Component X', 'Industrial component with warranty')");
    }

    // Seed users if empty
    $ucount = $pdo->query("SELECT COUNT(*) FROM users")->fetchColumn();
    if ($ucount == 0) {
        $pdo->exec("INSERT INTO users (username, password) VALUES
            ('admin', 'adminpass123'),
            ('dbuser', 'Sup3rS3cr3t!'),
            ('webmaster', 'webmaster2024')");
    }

} catch (Exception $e) {
    die('DB init error: ' . $e->getMessage());
}

// Handle search
$search = isset($_GET['search']) ? $_GET['search'] : '';
$results = [];
$error = null;

if ($search !== '') {
    // VULNERABLE: direct string concatenation into SQL query - no sanitization
    $query = "SELECT name, description FROM products WHERE name LIKE '%" . $search . "%'";
    try {
        $stmt = $pdo->query($query);
        $results = $stmt->fetchAll(PDO::FETCH_ASSOC);
    } catch (Exception $e) {
        $error = $e->getMessage();
    }
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Product Catalog</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #f4f4f4; }
        h1 { color: #333; }
        form { margin-bottom: 20px; }
        input[type=text] { padding: 8px; width: 300px; border: 1px solid #ccc; border-radius: 4px; }
        input[type=submit] { padding: 8px 16px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
        input[type=submit]:hover { background: #0056b3; }
        table { border-collapse: collapse; width: 100%; background: white; }
        th, td { border: 1px solid #ddd; padding: 10px; text-align: left; }
        th { background: #007bff; color: white; }
        tr:nth-child(even) { background: #f9f9f9; }
        .error { color: red; background: #ffe0e0; padding: 10px; border-radius: 4px; margin-bottom: 10px; }
        .no-results { color: #666; }
    </style>
</head>
<body>
    <h1>Product Catalog</h1>
    <form method="GET" action="/index.php">
        <label for="search">Search Products:</label><br><br>
        <input type="text" id="search" name="search" value="<?php echo htmlspecialchars($search, ENT_QUOTES); ?>" placeholder="Enter product name...">
        <input type="submit" value="Search">
    </form>

    <?php if ($error): ?>
    <div class="error"><strong>Database Error:</strong> <?php echo $error; ?></div>
    <?php endif; ?>

    <?php if ($search !== ''): ?>
    <h2>Search Results for: "<?php echo htmlspecialchars($search, ENT_QUOTES); ?>"</h2>
    <?php if (count($results) > 0): ?>
    <table>
        <thead>
            <tr>
                <th>Product Name</th>
                <th>Description</th>
            </tr>
        </thead>
        <tbody>
            <?php foreach ($results as $row): ?>
            <tr>
                <td><?php echo $row['name']; ?></td>
                <td><?php echo $row['description']; ?></td>
            </tr>
            <?php endforeach; ?>
        </tbody>
    </table>
    <?php else: ?>
    <p class="no-results">No products found matching your search.</p>
    <?php endif; ?>
    <?php else: ?>
    <h2>All Products</h2>
    <?php
    $all = $pdo->query("SELECT name, description FROM products")->fetchAll(PDO::FETCH_ASSOC);
    ?>
    <table>
        <thead>
            <tr>
                <th>Product Name</th>
                <th>Description</th>
            </tr>
        </thead>
        <tbody>
            <?php foreach ($all as $row): ?>
            <tr>
                <td><?php echo $row['name']; ?></td>
                <td><?php echo $row['description']; ?></td>
            </tr>
            <?php endforeach; ?>
        </tbody>
    </table>
    <?php endif; ?>

    <br>
    <p><em>Search the catalog by entering a product name above.</em></p>
</body>
</html>
