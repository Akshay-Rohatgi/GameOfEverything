<?php
$dbPath = '/var/db/app.db';
if (!is_dir('/var/db')) {
    mkdir('/var/db', 0755, true);
}
$db = new PDO('sqlite:' . $dbPath);
$db->exec('CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(128), description VARCHAR(256))');
$db->exec('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username VARCHAR(64), password VARCHAR(128))');
$pc = $db->query('SELECT COUNT(*) FROM products')->fetchColumn();
if ($pc == 0) {
    $db->exec("INSERT INTO products (name, description) VALUES ('Widget Alpha', 'A standard aluminum widget'), ('Widget Beta', 'An upgraded polymer widget'), ('Gadget Pro', 'Professional grade gadget'), ('Gadget Lite', 'Lightweight consumer gadget'), ('SuperTool', 'Multi-purpose tool for professionals')");
}
$uc = $db->query('SELECT COUNT(*) FROM users')->fetchColumn();
if ($uc == 0) {
    $db->exec("INSERT INTO users (username, password) VALUES ('admin', 'S3cur3P@ssw0rd'), ('webmaster', 'webmaster123'), ('dbuser', 'dbpassword99')");
}
$uri = strtok($_SERVER['REQUEST_URI'], '?');
if ($uri === '/login.php' || $uri === '/login') {
    $error = '';
    $success = '';
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $username = isset($_POST['username']) ? $_POST['username'] : '';
        $password = isset($_POST['password']) ? $_POST['password'] : '';
        $stmt = $db->prepare('SELECT * FROM users WHERE username = ? AND password = ?');
        $stmt->execute([$username, $password]);
        $user = $stmt->fetch(PDO::FETCH_ASSOC);
        if ($user) {
            $success = 'Welcome, ' . htmlspecialchars($user['username']) . '!';
        } else {
            $error = 'Invalid username or password.';
        }
    }
    echo '<!DOCTYPE html><html><head><title>Login</title><style>body{font-family:Arial,sans-serif;margin:40px;}.login-box{max-width:350px;margin:0 auto;border:1px solid #ccc;padding:30px;border-radius:4px;}h2{text-align:center;}input[type=text],input[type=password]{width:100%;padding:8px;margin:6px 0 16px 0;box-sizing:border-box;}input[type=submit]{width:100%;padding:10px;background:#4a90d9;color:white;border:none;cursor:pointer;font-size:16px;}.error{color:red;}.success{color:green;}a{color:#4a90d9;}</style></head><body><div class="login-box"><h2>Login</h2><p><a href="/index.php">Back to Search</a></p>';
    if ($error) echo '<p class="error">' . htmlspecialchars($error) . '</p>';
    if ($success) echo '<p class="success">' . $success . '</p>';
    echo '<form method="POST" action="/login.php"><label>Username:</label><input type="text" name="username" required><label>Password:</label><input type="password" name="password" required><input type="submit" value="Login"></form></div></body></html>';
} else {
    echo '<!DOCTYPE html><html><head><title>Product Catalog</title><style>body{font-family:Arial,sans-serif;margin:40px;}table{border-collapse:collapse;width:100%;}th,td{border:1px solid #ccc;padding:8px 12px;text-align:left;}th{background:#4a90d9;color:white;}tr:nth-child(even){background:#f2f2f2;}.search-form{margin-bottom:20px;}input[type=text]{padding:6px;width:300px;}input[type=submit]{padding:6px 16px;background:#4a90d9;color:white;border:none;cursor:pointer;}a{color:#4a90d9;}</style></head><body><h1>Product Catalog</h1><p><a href="/login.php">Login</a></p><div class="search-form"><form method="GET" action="/index.php"><label for="search">Search Products:</label><br><br><input type="text" id="search" name="search" placeholder="Enter product name..." value="' . (isset($_GET['search']) ? htmlspecialchars($_GET['search'], ENT_QUOTES) : '') . '"><input type="submit" value="Search"></form></div>';
    if (isset($_GET['search'])) {
        $search = $_GET['search'];
        $query = "SELECT name, description FROM products WHERE name LIKE '%" . $search . "%'";
        $result = $db->query($query);
        if ($result === false) {
            $info = $db->errorInfo();
            echo '<p style="color:red;">Query error: ' . htmlspecialchars($info[2]) . '</p>';
        } else {
            $rows = $result->fetchAll(PDO::FETCH_ASSOC);
            if (count($rows) === 0) {
                echo '<p>No products found.</p>';
            } else {
                echo '<table><tr><th>Name</th><th>Description</th></tr>';
                foreach ($rows as $row) {
                    echo '<tr><td>' . $row['name'] . '</td><td>' . $row['description'] . '</td></tr>';
                }
                echo '</table>';
            }
        }
    } else {
        echo '<p>Enter a search term above to find products.</p>';
    }
    echo '</body></html>';
}
?>