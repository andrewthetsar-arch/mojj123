
<?php

$backend_url = 'http://127.0.0.1:8000' . $_SERVER['REQUEST_URI'];



$ch = curl_init($backend_url);

curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);

curl_setopt($ch, CURLOPT_HEADER, false);

curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $_SERVER['REQUEST_METHOD']);

curl_setopt($ch, CURLOPT_TIMEOUT, 30);



$body = file_get_contents('php://input');

if (!empty($body)) {

    curl_setopt($ch, CURLOPT_POSTFIELDS, $body);

}



$headers = ['Expect:'];

foreach (getallheaders() as $key => $val) {

    if (strtolower($key) !== 'host' && strtolower($key) !== 'expect') {

        $headers[] = "$key: $val";

    }

}

curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);



$response = curl_exec($ch);

$http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);



if ($response === false) {

    http_response_code(502);

    header('Content-Type: application/json; charset=utf-8');

    echo json_encode(['detail' => 'Бэкенд недоступен']);

} else {

    http_response_code($http_code);

    header('Content-Type: application/json; charset=utf-8');

    echo $response;

}

curl_close($ch);

