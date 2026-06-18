<?php
$dbfile = '/var/db/app.db';
if (!is_dir('/var/db')) {
    mkdir('/var/db', 0755, true);
}
$db = new PDO('sqlite:' . $dbfile);
$db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
$db->exec("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(128), description VARCHAR(256))");
$db->exec("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username VARCHAR(64), password VARCHAR(128))");
$count = $db->query('SELECT COUNT(*) FROM products')->fetchColumn();
if ((int)$count === 0) {
    $db->exec("INSERT INTO products (name, description) VALUES ('Widget A', 'A sturdy metal widget'), ('Widget B', 'A lightweight plastic widget'), ('Gadget X', 'An electronic gadget with many features'), ('Gadget Y', 'A compact portable gadget')");
}
$count = $db->query('SELECT COUNT(*) FROM users')->fetchColumn();
if ((int)$count === 0) {
    $db->exec("INSERT INTO users (username, password) VALUES ('admin', 'S3cr3tP@ss!'), ('alice', 'password123'), ('bob', 'letmein')");
}

$request_uri = strtok($_SERVER['REQUEST_URI'], '?');

if ($request_uri === '/search.php' || $request_uri === '/search') {
    $search = isset($_GET['search']) ? $_GET['search'] : '';
    $query = "SELECT name, description FROM products WHERE name LIKE '%" . $search . "%'";
    try {
        $results = $db->query($query);
    } catch (Exception $e) {
        $results = false;
        $error = $e->getMessage();
    }
    echo '<!DOCTYPE html><html><head><title>ShopDB - Search Results</title></head><body>';
    echo '<h1>Search Results</h1>';
    echo '<form action="/search.php" method="GET">';
    echo '<input type="text" name="search" value="' . htmlspecialchars($search) . '" placeholder="Search products...">';
    echo '<button type="submit">Search</button></form>';
    echo '<a href="/login.php">Login</a> | <a href="/index.php">Home</a>';
    if (isset($error)) {
        echo '<p style="color:red">Query error: ' . htmlspecialchars($error) . '</p>';
    }
    echo '<table border="1" cellpadding="5" cellspacing="0"><tr><th>Name</th><th>Description</th></tr>';
    if ($results) {
        foreach ($results as $row) {
            echo '<tr><td>' . $row[0] . '</td><td>' . $row[1] . '</td></tr>';
        }
    }
    echo '</table></body></html>';
} elseif ($request_uri === '/login.php' || $request_uri === '/login') {
    $message = '';
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $username = isset($_POST['username']) ? $_POST['username'] : '';
        $password = isset($_POST['password']) ? $_POST['password'] : '';
        $stmt = $db->prepare('SELECT id FROM users WHERE username = ? AND password = ?');
        $stmt->execute([$username, $password]);
        $user = $stmt->fetch();
        if ($user) {
            $message = '<p style="color:green">Login successful! Welcome, ' . htmlspecialchars($username) . '.</p>';
        } else {
            $message = '<p style="color:red">Invalid username or password.</p>';
        }
    }
    echo '<!DOCTYPE html><html><head><title>ShopDB - Login</title></head><body>';
    echo '<h1>Login</h1>';
    echo $message;
    echo '<form action="/login.php" method="POST">';
    echo '<label>Username: <input type="text" name="username"></label><br><br>';
    echo '<label>Password: <input type="password" name="password"></label><br><br>';
    echo '<button type="submit">Login</button></form>';
    echo '<p><a href="/index.php">Home</a></p>';
    echo '</body></html>';
} else {
    echo '<!DOCTYPE html><html><head><title>ShopDB - Products</title></head><body>';
    echo '<h1>Welcome to ShopDB</h1>';
    echo '<form action="/search.php" method="GET">';
    echo '<input type="text" name="search" placeholder="Search products...">';
    echo '<button type="submit">Search</button></form>';
    echo '<p><a href="/login.php">Login</a></p>';
    echo '</body></html>';
}
?>
