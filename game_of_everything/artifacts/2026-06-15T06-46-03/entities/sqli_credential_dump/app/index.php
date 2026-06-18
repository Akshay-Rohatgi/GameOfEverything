<?php
$db_host = '127.0.0.1';
$db_user = 'appuser';
$db_pass = 'AppPass123!';
$db_name = 'shopdb';

$conn = new mysqli($db_host, $db_user, $db_pass, $db_name);
if ($conn->connect_error) {
    die('Connection failed');
}

$results = [];
$searched = false;

if (isset($_GET['search'])) {
    $searched = true;
    $search = $_GET['search'];
    $query = "SELECT name, description FROM products WHERE name LIKE '%" . $search . "%'";
    $result = $conn->query($query);
    if ($result) {
        while ($row = $result->fetch_assoc()) {
            $results[] = $row;
        }
    }
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>ShopLocal - Product Catalog</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
        h1 { color: #333; }
        .search-box { margin: 20px 0; }
        .search-box input[type=text] { padding: 8px; width: 300px; font-size: 16px; border: 1px solid #ccc; border-radius: 4px; }
        .search-box input[type=submit] { padding: 8px 16px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
        table { border-collapse: collapse; width: 100%; background: white; }
        th { background: #007bff; color: white; padding: 10px; text-align: left; }
        td { padding: 10px; border-bottom: 1px solid #ddd; }
        tr:hover { background: #f0f0f0; }
        .no-results { color: #888; margin-top: 10px; }
    </style>
</head>
<body>
    <h1>ShopLocal Product Catalog</h1>
    <div class="search-box">
        <form method="GET" action="/index.php">
            <input type="text" name="search" placeholder="Search products..." value="<?php echo isset($_GET['search']) ? htmlspecialchars($_GET['search'], ENT_QUOTES) : ''; ?>">
            <input type="submit" value="Search">
        </form>
    </div>
    <?php if ($searched): ?>
    <h2>Search Results</h2>
    <?php if (count($results) > 0): ?>
    <table>
        <thead>
            <tr><th>Name</th><th>Description</th></tr>
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
    <p>Enter a product name above to search our catalog.</p>
    <?php endif; ?>
</body>
</html>
