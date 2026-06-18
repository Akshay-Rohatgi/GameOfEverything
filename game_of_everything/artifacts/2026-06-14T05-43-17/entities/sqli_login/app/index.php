<?php
$db_path = '/var/db/app.db';
if (!file_exists($db_path)) {
    @mkdir('/var/db', 0777, true);
    $db = new PDO('sqlite:' . $db_path);
    $db->exec("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username VARCHAR(64) NOT NULL, password VARCHAR(128) NOT NULL, role VARCHAR(32) NOT NULL DEFAULT 'user')");
    $db->exec("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(128) NOT NULL, description TEXT)");
    $db->exec("INSERT INTO users (username, password, role) VALUES ('admin', 'SuperSecret123', 'administrator'), ('alice', 'password1', 'user'), ('bob', 'letmein', 'user')");
    $db->exec("INSERT INTO products (name, description) VALUES ('Widget A', 'A standard widget'), ('Widget B', 'An improved widget'), ('Gadget X', 'A fancy gadget'), ('Gadget Y', 'An advanced gadget')");
} else {
    $db = new PDO('sqlite:' . $db_path);
}

$page = isset($_GET['page']) ? $_GET['page'] : 'home';
$message = '';
$username_out = '';
$role_out = '';
$search_results = [];
$search = '';

if ($page === 'login' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $username = $_POST['username'];
    $password = $_POST['password'];
    $query = "SELECT username, role FROM users WHERE username='" . $username . "' AND password='" . $password . "'";
    $result = $db->query($query);
    if ($result) {
        $row = $result->fetch(PDO::FETCH_ASSOC);
        if ($row) {
            $username_out = $row['username'];
            $role_out = $row['role'];
            $message = 'Login successful!';
        } else {
            $message = 'Invalid credentials.';
        }
    } else {
        $message = 'Query error.';
    }
}

if ($page === 'search') {
    $search = isset($_GET['q']) ? $_GET['q'] : '';
    $stmt = $db->prepare('SELECT name, description FROM products WHERE name LIKE :search');
    $stmt->execute([':search' => '%' . $search . '%']);
    $search_results = $stmt->fetchAll(PDO::FETCH_ASSOC);
}
?>
<!DOCTYPE html>
<html>
<head><title>Product Store</title></head>
<body>
<?php if ($page === 'home'): ?>
<h1>Welcome to Product Store</h1>
<p><a href="?page=search">Search Products</a> | <a href="?page=login">Login</a></p>
<?php elseif ($page === 'search'): ?>
<h1>Product Search</h1>
<form method="GET" action="/index.php">
  <input type="hidden" name="page" value="search">
  <input type="text" name="q" value="<?php echo htmlspecialchars($search); ?>" placeholder="Search...">
  <input type="submit" value="Search">
</form>
<?php if (!empty($search_results)): ?>
<table border="1">
<tr><th>Name</th><th>Description</th></tr>
<?php foreach ($search_results as $row): ?>
<tr><td><?php echo htmlspecialchars($row['name']); ?></td><td><?php echo htmlspecialchars($row['description']); ?></td></tr>
<?php endforeach; ?>
</table>
<?php else: ?>
<p>No results found.</p>
<?php endif; ?>
<p><a href="/index.php">Home</a></p>
<?php elseif ($page === 'login'): ?>
<h1>Login</h1>
<?php if ($message): ?>
<p><?php echo $message; ?></p>
<?php if ($username_out): ?>
<p>Welcome, <?php echo $username_out; ?></p>
<p>Role: <?php echo $role_out; ?></p>
<?php endif; ?>
<?php endif; ?>
<form method="POST" action="/index.php?page=login">
  <label>Username: <input type="text" name="username"></label><br>
  <label>Password: <input type="password" name="password"></label><br>
  <input type="submit" value="Login">
</form>
<p><a href="/index.php">Home</a></p>
<?php endif; ?>
</body>
</html>
