<?php
$db_path = '/var/db/app.db';
try {
    $pdo = new PDO('sqlite:' . $db_path);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
} catch (Exception $e) {
    die('DB connection failed: ' . $e->getMessage());
}

$search = isset($_GET['search']) ? $_GET['search'] : '';
$results = [];
$error = '';

if ($search !== '') {
    $query = "SELECT name, description FROM products WHERE name LIKE '%" . $search . "%'";
    try {
        $stmt = $pdo->query($query);
        $results = $stmt->fetchAll(PDO::FETCH_NUM);
    } catch (Exception $e) {
        $error = 'SQL Error: ' . $e->getMessage();
    }
}
?>
<!DOCTYPE html>
<html>
<head>
    <title>Product Catalog</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        h1 { color: #333; }
        form { margin-bottom: 20px; }
        input[type=text] { padding: 6px; width: 300px; }
        input[type=submit] { padding: 6px 16px; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #ccc; padding: 8px 12px; text-align: left; }
        th { background: #f0f0f0; }
        .error { color: red; }
    </style>
</head>
<body>
    <h1>Product Catalog</h1>
    <form method="GET" action="/search.php">
        <input type="text" name="search" placeholder="Search products..." value="<?php echo htmlspecialchars($search, ENT_QUOTES); ?>">
        <input type="submit" value="Search">
    </form>
<?php if ($error): ?>
    <p class="error"><?php echo $error; ?></p>
<?php endif; ?>
<?php if ($search !== ''): ?>
    <h2>Search Results</h2>
    <table>
        <tr><th>Name</th><th>Description</th></tr>
<?php foreach ($results as $row): ?>
        <tr>
            <td><?php echo htmlspecialchars($row[0], ENT_QUOTES); ?></td>
            <td><?php echo htmlspecialchars($row[1], ENT_QUOTES); ?></td>
        </tr>
<?php endforeach; ?>
<?php if (empty($results) && !$error): ?>
        <tr><td colspan="2">No results found.</td></tr>
<?php endif; ?>
    </table>
<?php else: ?>
    <h2>All Products</h2>
    <table>
        <tr><th>Name</th><th>Description</th></tr>
<?php
    try {
        $stmt = $pdo->query("SELECT name, description FROM products");
        $all = $stmt->fetchAll(PDO::FETCH_NUM);
        foreach ($all as $row) {
            echo '<tr><td>' . htmlspecialchars($row[0], ENT_QUOTES) . '</td><td>' . htmlspecialchars($row[1], ENT_QUOTES) . '</td></tr>' . "\n";
        }
    } catch (Exception $e) {
        echo '<tr><td colspan="2">Error loading products.</td></tr>';
    }
?>
    </table>
<?php endif; ?>
</body>
</html>
