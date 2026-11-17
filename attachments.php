<?php
declare(strict_types=1);

const EXTRACTOR_URL = 'http://127.0.0.1:8000/extract';
const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;

const TEXT_EXTENSIONS = [
    'txt', 'md', 'php', 'html', 'htm', 'css', 'js', 'ts',
    'py', 'json', 'sql', 'xml', 'yml', 'yaml', 'log', 'csv',
];

const DOCUMENT_EXTENSIONS = ['pdf', 'docx', 'xlsx', 'xlsm', 'pptx'];

const IMAGE_TYPES = [
    'jpg' => 'image/jpeg',
    'jpeg' => 'image/jpeg',
    'png' => 'image/png',
    'webp' => 'image/webp',
];

/**
 * @return list<string>
 */
function acceptedExtensions(): array
{
    return array_merge(array_keys(IMAGE_TYPES), TEXT_EXTENSIONS, DOCUMENT_EXTENSIONS);
}

/**
 * @return array{error: string, text: string, images: list<string>}
 */
function readAttachment(?array $file, string $label): array
{
    $result = ['error' => '', 'text' => '', 'images' => []];

    if (!$file || ($file['error'] ?? UPLOAD_ERR_NO_FILE) === UPLOAD_ERR_NO_FILE) {
        return $result;
    }

    if ($file['error'] === UPLOAD_ERR_INI_SIZE || $file['error'] === UPLOAD_ERR_FORM_SIZE) {
        $result['error'] = "Le fichier « $label » dépasse la limite du serveur ("
            . ini_get('upload_max_filesize') . ').';
        return $result;
    }

    if ($file['error'] !== UPLOAD_ERR_OK) {
        $result['error'] = "Le fichier « $label » n'a pas pu être téléversé.";
        return $result;
    }

    if ($file['size'] > MAX_ATTACHMENT_BYTES) {
        $result['error'] = "Le fichier « $label » ne doit pas dépasser 5 Mo.";
        return $result;
    }

    $fileName = basename((string) $file['name']);
    $extension = strtolower(pathinfo($fileName, PATHINFO_EXTENSION));

    if (isset(IMAGE_TYPES[$extension])) {
        $finfo = finfo_open(FILEINFO_MIME_TYPE);
        $mimeType = $finfo ? finfo_file($finfo, $file['tmp_name']) : false;
        if ($finfo) {
            finfo_close($finfo);
        }

        if ($mimeType !== IMAGE_TYPES[$extension]) {
            $result['error'] = "Le format réel de l'image « $label » ne correspond pas à son extension.";
            return $result;
        }

        $bytes = file_get_contents($file['tmp_name']);
        if ($bytes === false) {
            $result['error'] = "Impossible de lire l'image « $label ».";
            return $result;
        }

        $result['images'][] = 'data:' . $mimeType . ';base64,' . base64_encode($bytes);
        return $result;
    }

    $accepted = acceptedExtensions();
    if (!in_array($extension, $accepted, true)) {
        $result['error'] = "Extension non prise en charge pour « $label »: "
            . ($extension !== '' ? $extension : 'fichier sans extension')
            . '. Formats acceptés: ' . implode(', ', $accepted) . '.';
        return $result;
    }

    $extracted = requestExtractor($file['tmp_name'], $fileName);
    if ($extracted['error'] !== '') {
        $result['error'] = "« $label »: " . $extracted['error'];
        return $result;
    }

    $result['text'] = $extracted['text'];
    foreach ($extracted['images'] as $imageBase64) {
        $result['images'][] = 'data:image/png;base64,' . $imageBase64;
    }

    if ($result['text'] === '' && $result['images'] === []) {
        $result['error'] = "« $label » ne contient aucun contenu lisible.";
    }

    return $result;
}

/**
 * @return array{error: string, text: string, images: list<string>}
 */
function requestExtractor(string $tmpPath, string $fileName): array
{
    $ch = curl_init(EXTRACTOR_URL);
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => ['file' => new CURLFile($tmpPath, 'application/octet-stream', $fileName)],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => 5,
        CURLOPT_TIMEOUT => 120,
    ]);

    $body = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $curlError = curl_error($ch);
    curl_close($ch);

    if ($body === false) {
        return [
            'error' => "Le service Python d'extraction est injoignable ($curlError). "
                . "Démarrez-le avec extractor\\start.bat puis relancez l'évaluation.",
            'text' => '',
            'images' => [],
        ];
    }

    $data = json_decode((string) $body, true);
    if ($httpCode < 200 || $httpCode >= 300) {
        $detail = is_array($data) ? ($data['detail'] ?? '') : '';
        return [
            'error' => $detail !== '' ? (string) $detail : "Le service d'extraction a refusé le fichier (HTTP $httpCode).",
            'text' => '',
            'images' => [],
        ];
    }

    return [
        'error' => '',
        'text' => is_array($data) ? trim((string) ($data['text'] ?? '')) : '',
        'images' => is_array($data) && is_array($data['images'] ?? null) ? $data['images'] : [],
    ];
}
