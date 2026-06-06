/*
 * Benchmark Backend – C (libmicrohttpd + OpenSSL)
 * ================================================
 * Réplica fiel del servidor Python/FastAPI de hashing intensivo.
 *
 * Endpoints:
 *   POST /hash-seq   → una petición a la vez (mutex global)
 *   POST /hash-conc  → paralelo (threadpool de libmicrohttpd)
 *   GET  /health     → health check
 *
 * Cuerpo JSON esperado:
 *   { "text": "cadena", "iterations": 10000 }
 *
 * Compilar:
 *   gcc -O2 -o hash_benchmark hash_benchmark.c \
 *       -lmicrohttpd -lssl -lcrypto -lpthread -lcjson
 *
 * Dependencias (Ubuntu/Debian):
 *   sudo apt install libmicrohttpd-dev libssl-dev libcjson-dev
 */
 
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <ctype.h>
#include <pthread.h>
 
#include <microhttpd.h>
#include <openssl/sha.h>
#include <cjson/cJSON.h>
 
/* -------------------------------------------------------------------------
 * Configuración
 * ---------------------------------------------------------------------- */
 
#define PORT          8004
#define MAX_BODY_SIZE (1024 * 64)   /* 64 KB – suficiente para el JSON */
#define MAX_ITER      500000
#define MIN_ITER      1
 
/* Equivalente al asyncio.Lock() de Python */
static pthread_mutex_t hash_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t stringproc_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t prime_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t jsonproc_lock = PTHREAD_MUTEX_INITIALIZER;

#define MAX_LIMIT      100000000
#define MAX_JSON_COUNT 50000
#define MAX_JSON_NESTED 50
 
/* -------------------------------------------------------------------------
 * Utilidades
 * ---------------------------------------------------------------------- */
 
/* Convierte bytes a hex string. dst debe tener al menos len*2+1 bytes. */
static void bytes_to_hex(const uint8_t *src, size_t len, char *dst)
{
    static const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < len; i++) {
        dst[i * 2]     = hex[(src[i] >> 4) & 0xF];
        dst[i * 2 + 1] = hex[src[i] & 0xF];
    }
    dst[len * 2] = '\0';
}
 
/* -------------------------------------------------------------------------
 * Lógica de cómputo  (CPU-bound, equivalente a compute_hash() en Python)
 * ---------------------------------------------------------------------- */
 
/*
 * Aplica SHA-256 de forma encadenada `iterations` veces.
 * out_hex debe apuntar a un buffer de al menos SHA256_DIGEST_LENGTH*2+1 bytes.
 */
static void compute_hash(const char *text, int iterations, char *out_hex)
{
    uint8_t digest[SHA256_DIGEST_LENGTH];
    uint8_t input[SHA256_DIGEST_LENGTH];
    size_t  input_len = strlen(text);
 
    /* Primera ronda: texto → bytes */
    SHA256((const uint8_t *)text, input_len, digest);
    iterations--;
 
    /* Rondas restantes: digest anterior → nuevo digest */
    while (iterations-- > 0) {
        memcpy(input, digest, SHA256_DIGEST_LENGTH);
        SHA256(input, SHA256_DIGEST_LENGTH, digest);
    }
 
    bytes_to_hex(digest, SHA256_DIGEST_LENGTH, out_hex);
}

static void compute_string_proc(const char *text,
                                 char *reversed, char *uppercase, int *vowel_count,
                                 size_t text_len)
{
    for (size_t i = 0; i < text_len; i++) {
        reversed[i] = text[text_len - 1 - i];
        uppercase[i] = (char)toupper((unsigned char)text[i]);
    }
    reversed[text_len] = '\0';
    uppercase[text_len] = '\0';

    *vowel_count = 0;
    for (size_t i = 0; i < text_len; i++) {
        char c = (char)tolower((unsigned char)text[i]);
        if (c == 'a' || c == 'e' || c == 'i' || c == 'o' || c == 'u') {
            (*vowel_count)++;
        }
    }
}

static void compute_prime(int limit, int *prime_count, int *largest_prime)
{
    uint8_t *sieve = calloc((size_t)limit + 1, 1);
    if (!sieve) {
        *prime_count = 0;
        *largest_prime = 0;
        return;
    }
    if (limit >= 2) sieve[2] = 1;
    for (int i = 3; i <= limit; i += 2) sieve[i] = 1;

    for (int i = 3; (long long)i * i <= limit; i += 2) {
        if (sieve[i]) {
            for (long long j = (long long)i * i; j <= limit; j += i) {
                sieve[j] = 0;
            }
        }
    }

    *prime_count = (limit >= 2) ? 1 : 0;
    *largest_prime = (limit >= 2) ? 2 : 0;
    for (int i = 3; i <= limit; i += 2) {
        if (sieve[i]) {
            (*prime_count)++;
            *largest_prime = i;
        }
    }
    free(sieve);
}

static void compute_json_proc(int count, int nested,
                               long *serialized_size, char *checksum)
{
    cJSON *root = cJSON_CreateArray();
    for (int i = 0; i < count; i++) {
        cJSON *inner = NULL;
        for (int j = nested - 1; j >= 0; j--) {
            char id_buf[64];
            snprintf(id_buf, sizeof(id_buf), "item_%d_%d", i, j);
            cJSON *obj = cJSON_CreateObject();
            cJSON_AddStringToObject(obj, "id", id_buf);
            cJSON_AddNumberToObject(obj, "value", i * nested + j);
            if (inner) {
                cJSON_AddItemToObject(obj, "inner", inner);
            }
            inner = obj;
        }
        char outer_buf[64];
        snprintf(outer_buf, sizeof(outer_buf), "item_%d", i);
        cJSON *item = cJSON_CreateObject();
        cJSON_AddStringToObject(item, "outer", outer_buf);
        if (inner) {
            cJSON_AddItemToObject(item, "inner", inner);
        }
        cJSON_AddItemToArray(root, item);
    }

    char *json_str = cJSON_PrintUnformatted(root);
    *serialized_size = (long)strlen(json_str);

    cJSON *parsed = cJSON_Parse(json_str);

    uint8_t digest[SHA256_DIGEST_LENGTH];
    SHA256((const uint8_t *)json_str, strlen(json_str), digest);
    bytes_to_hex(digest, SHA256_DIGEST_LENGTH, checksum);

    free(json_str);
    if (parsed) cJSON_Delete(parsed);
    cJSON_Delete(root);
}
 
/* -------------------------------------------------------------------------
 * Acumulador de body para peticiones POST
 * ---------------------------------------------------------------------- */
 
typedef struct {
    char  *data;
    size_t size;
} RequestBody;
 
static void request_body_free(RequestBody *rb)
{
    if (rb) {
        free(rb->data);
        free(rb);
    }
}
 
/* -------------------------------------------------------------------------
 * Construcción de respuestas JSON
 * ---------------------------------------------------------------------- */
 
static struct MHD_Response *make_json_response(const char *json_str, int *status)
{
    *status = MHD_HTTP_OK;
    struct MHD_Response *resp = MHD_create_response_from_buffer(
        strlen(json_str),
        (void *)json_str,
        MHD_RESPMEM_MUST_COPY
    );
    MHD_add_response_header(resp, "Content-Type", "application/json");
    return resp;
}
 
static struct MHD_Response *make_error_response(const char *msg, int *status)
{
    *status = MHD_HTTP_BAD_REQUEST;
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "error", msg);
    char *json_str = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
 
    struct MHD_Response *resp = MHD_create_response_from_buffer(
        strlen(json_str), json_str, MHD_RESPMEM_MUST_COPY
    );
    MHD_add_response_header(resp, "Content-Type", "application/json");
    free(json_str);
    return resp;
}
 
/* -------------------------------------------------------------------------
 * Parseo del body JSON y validación de campos
 * ---------------------------------------------------------------------- */
 
/*
 * Retorna 0 en éxito y rellena text_out / iter_out.
 * text_out apunta al interior del cJSON; no liberar por separado.
 */
static int parse_body(const char *body, cJSON **root_out,
                      const char **text_out, int *iter_out,
                      char *err_msg, size_t err_sz)
{
    *root_out = cJSON_Parse(body);
    if (!*root_out) {
        snprintf(err_msg, err_sz, "JSON inválido");
        return -1;
    }
 
    cJSON *j_text = cJSON_GetObjectItemCaseSensitive(*root_out, "text");
    cJSON *j_iter = cJSON_GetObjectItemCaseSensitive(*root_out, "iterations");
 
    if (!cJSON_IsString(j_text) || !j_text->valuestring || j_text->valuestring[0] == '\0') {
        snprintf(err_msg, err_sz, "Campo 'text' requerido y no vacío");
        cJSON_Delete(*root_out);
        return -1;
    }
 
    int iters = 10000; /* valor por defecto */
    if (j_iter) {
        if (!cJSON_IsNumber(j_iter)) {
            snprintf(err_msg, err_sz, "Campo 'iterations' debe ser número");
            cJSON_Delete(*root_out);
            return -1;
        }
        iters = (int)j_iter->valuedouble;
        if (iters < MIN_ITER || iters > MAX_ITER) {
            snprintf(err_msg, err_sz,
                     "iterations debe estar entre %d y %d", MIN_ITER, MAX_ITER);
            cJSON_Delete(*root_out);
            return -1;
        }
    }
 
    *text_out = j_text->valuestring;
    *iter_out = iters;
    return 0;
}
 
/* -------------------------------------------------------------------------
 * Handler principal de libmicrohttpd
 * ---------------------------------------------------------------------- */
 
static enum MHD_Result answer_to_connection(
    void *cls,
    struct MHD_Connection *connection,
    const char *url,
    const char *method,
    const char *version,
    const char *upload_data,
    size_t *upload_data_size,
    void **con_cls)
{
    (void)cls; (void)version;
 
    /* ── Primera llamada: inicializar acumulador de body ── */
    if (*con_cls == NULL) {
        RequestBody *rb = calloc(1, sizeof(RequestBody));
        if (!rb) return MHD_NO;
        *con_cls = rb;
        return MHD_YES;
    }
 
    RequestBody *rb = (RequestBody *)*con_cls;
 
    /* ── Acumular body del POST ── */
    if (*upload_data_size > 0) {
        if (rb->size + *upload_data_size > MAX_BODY_SIZE) {
            /* Payload demasiado grande */
            int status;
            struct MHD_Response *resp =
                make_error_response("Payload demasiado grande", &status);
            enum MHD_Result ret = MHD_queue_response(connection, status, resp);
            MHD_destroy_response(resp);
            return ret;
        }
        rb->data = realloc(rb->data, rb->size + *upload_data_size + 1);
        memcpy(rb->data + rb->size, upload_data, *upload_data_size);
        rb->size += *upload_data_size;
        rb->data[rb->size] = '\0';
        *upload_data_size = 0;
        return MHD_YES;
    }
 
    /* ── Ruteo ── */
    struct MHD_Response *response = NULL;
    int http_status = MHD_HTTP_OK;
 
    /* GET /health */
    if (strcmp(method, "GET") == 0 && strcmp(url, "/health") == 0) {
        const char *body = "{\"status\":\"ok\",\"language\":\"c\"}";
        response = MHD_create_response_from_buffer(
            strlen(body), (void *)body, MHD_RESPMEM_PERSISTENT
        );
        MHD_add_response_header(response, "Content-Type", "application/json");
 
    /* POST /hash-seq  o  POST /hash-conc */
    } else if (strcmp(method, "POST") == 0 &&
               (strcmp(url, "/hash-seq") == 0 || strcmp(url, "/hash-conc") == 0)) {
 
        if (!rb->data || rb->size == 0) {
            response = make_error_response("Body vacío", &http_status);
        } else {
            cJSON      *root    = NULL;
            const char *text    = NULL;
            int         iters   = 0;
            char        err[128]= {0};
 
            if (parse_body(rb->data, &root, &text, &iters, err, sizeof(err)) != 0) {
                response = make_error_response(err, &http_status);
            } else {
                /* Buffer para el hash hex */
                char hash_hex[SHA256_DIGEST_LENGTH * 2 + 1];
                int  is_seq = (strcmp(url, "/hash-seq") == 0);
 
                if (is_seq) {
                    /* Equivalente a `async with _sequential_lock:` */
                    pthread_mutex_lock(&hash_lock);
                    compute_hash(text, iters, hash_hex);
                    pthread_mutex_unlock(&hash_lock);
                } else {
                    /* Sin lock: paralelo */
                    compute_hash(text, iters, hash_hex);
                }
 
                /* Construir JSON de respuesta */
                cJSON *resp_json = cJSON_CreateObject();
                cJSON_AddStringToObject(resp_json, "mode",
                                        is_seq ? "sequential" : "concurrent");
                cJSON_AddNumberToObject(resp_json, "iterations", iters);
                cJSON_AddStringToObject(resp_json, "hash", hash_hex);
 
                char *json_str = cJSON_PrintUnformatted(resp_json);
                cJSON_Delete(resp_json);
 
                response = make_json_response(json_str, &http_status);
                free(json_str);
            }
            if (root) cJSON_Delete(root);
        }

    /* POST /stringproc-seq  o  POST /stringproc-conc */
    } else if (strcmp(method, "POST") == 0 &&
               (strcmp(url, "/stringproc-seq") == 0 || strcmp(url, "/stringproc-conc") == 0)) {

        if (!rb->data || rb->size == 0) {
            response = make_error_response("Body vacio", &http_status);
        } else {
            cJSON *root = cJSON_Parse(rb->data);
            if (!root) {
                response = make_error_response("JSON invalido", &http_status);
            } else {
                cJSON *j_text = cJSON_GetObjectItemCaseSensitive(root, "text");
                if (!cJSON_IsString(j_text) || !j_text->valuestring || j_text->valuestring[0] == '\0') {
                    response = make_error_response("Campo 'text' requerido", &http_status);
                } else {
                    const char *text = j_text->valuestring;
                    size_t   text_len = strlen(text);
                    int      is_seq   = (strcmp(url, "/stringproc-seq") == 0);

                    char reversed[MAX_BODY_SIZE];
                    char uppercase[MAX_BODY_SIZE];
                    int  vowel_count;

                    if (is_seq) {
                        pthread_mutex_lock(&stringproc_lock);
                        compute_string_proc(text, reversed, uppercase, &vowel_count, text_len);
                        pthread_mutex_unlock(&stringproc_lock);
                    } else {
                        compute_string_proc(text, reversed, uppercase, &vowel_count, text_len);
                    }

                    cJSON *resp_json = cJSON_CreateObject();
                    cJSON_AddStringToObject(resp_json, "mode", is_seq ? "sequential" : "concurrent");
                    cJSON_AddStringToObject(resp_json, "original", text);
                    cJSON_AddStringToObject(resp_json, "reversed", reversed);
                    cJSON_AddStringToObject(resp_json, "uppercase", uppercase);
                    cJSON_AddNumberToObject(resp_json, "vowel_count", vowel_count);

                    char *json_str = cJSON_PrintUnformatted(resp_json);
                    cJSON_Delete(resp_json);
                    response = make_json_response(json_str, &http_status);
                    free(json_str);
                }
                cJSON_Delete(root);
            }
        }

    /* POST /prime-seq  o  POST /prime-conc */
    } else if (strcmp(method, "POST") == 0 &&
               (strcmp(url, "/prime-seq") == 0 || strcmp(url, "/prime-conc") == 0)) {

        if (!rb->data || rb->size == 0) {
            response = make_error_response("Body vacio", &http_status);
        } else {
            cJSON *root = cJSON_Parse(rb->data);
            if (!root) {
                response = make_error_response("JSON invalido", &http_status);
            } else {
                cJSON *j_limit = cJSON_GetObjectItemCaseSensitive(root, "limit");
                int limit = 10000000;
                if (j_limit) {
                    if (!cJSON_IsNumber(j_limit)) {
                        response = make_error_response("Campo 'limit' debe ser numero", &http_status);
                    } else {
                        limit = (int)j_limit->valuedouble;
                        if (limit < 2 || limit > MAX_LIMIT) {
                            char err[128];
                            snprintf(err, sizeof(err), "limit debe estar entre 2 y %d", MAX_LIMIT);
                            response = make_error_response(err, &http_status);
                        }
                    }
                }
                if (!response) {
                    int is_seq = (strcmp(url, "/prime-seq") == 0);
                    int prime_count, largest_prime;

                    if (is_seq) {
                        pthread_mutex_lock(&prime_lock);
                        compute_prime(limit, &prime_count, &largest_prime);
                        pthread_mutex_unlock(&prime_lock);
                    } else {
                        compute_prime(limit, &prime_count, &largest_prime);
                    }

                    cJSON *resp_json = cJSON_CreateObject();
                    cJSON_AddStringToObject(resp_json, "mode", is_seq ? "sequential" : "concurrent");
                    cJSON_AddNumberToObject(resp_json, "limit", limit);
                    cJSON_AddNumberToObject(resp_json, "prime_count", prime_count);
                    cJSON_AddNumberToObject(resp_json, "largest_prime", largest_prime);

                    char *json_str = cJSON_PrintUnformatted(resp_json);
                    cJSON_Delete(resp_json);
                    response = make_json_response(json_str, &http_status);
                    free(json_str);
                }
                cJSON_Delete(root);
            }
        }

    /* POST /jsonproc-seq  o  POST /jsonproc-conc */
    } else if (strcmp(method, "POST") == 0 &&
               (strcmp(url, "/jsonproc-seq") == 0 || strcmp(url, "/jsonproc-conc") == 0)) {

        if (!rb->data || rb->size == 0) {
            response = make_error_response("Body vacio", &http_status);
        } else {
            cJSON *root = cJSON_Parse(rb->data);
            if (!root) {
                response = make_error_response("JSON invalido", &http_status);
            } else {
                cJSON *j_count  = cJSON_GetObjectItemCaseSensitive(root, "count");
                cJSON *j_nested = cJSON_GetObjectItemCaseSensitive(root, "nested");

                int count  = 5000;
                int nested = 10;
                int valid  = 1;

                if (j_count) {
                    if (!cJSON_IsNumber(j_count)) {
                        response = make_error_response("Campo 'count' debe ser numero", &http_status);
                        valid = 0;
                    } else {
                        count = (int)j_count->valuedouble;
                        if (count < 1 || count > MAX_JSON_COUNT) {
                            char err[128];
                            snprintf(err, sizeof(err), "count debe estar entre 1 y %d", MAX_JSON_COUNT);
                            response = make_error_response(err, &http_status);
                            valid = 0;
                        }
                    }
                }
                if (valid && j_nested) {
                    if (!cJSON_IsNumber(j_nested)) {
                        response = make_error_response("Campo 'nested' debe ser numero", &http_status);
                        valid = 0;
                    } else {
                        nested = (int)j_nested->valuedouble;
                        if (nested < 0 || nested > MAX_JSON_NESTED) {
                            response = make_error_response("nested debe estar entre 0 y 50", &http_status);
                            valid = 0;
                        }
                    }
                }

                if (valid && !response) {
                    int  is_seq = (strcmp(url, "/jsonproc-seq") == 0);
                    long serialized_size;
                    char checksum[SHA256_DIGEST_LENGTH * 2 + 1];

                    if (is_seq) {
                        pthread_mutex_lock(&jsonproc_lock);
                        compute_json_proc(count, nested, &serialized_size, checksum);
                        pthread_mutex_unlock(&jsonproc_lock);
                    } else {
                        compute_json_proc(count, nested, &serialized_size, checksum);
                    }

                    cJSON *resp_json = cJSON_CreateObject();
                    cJSON_AddStringToObject(resp_json, "mode", is_seq ? "sequential" : "concurrent");
                    cJSON_AddNumberToObject(resp_json, "serialized_bytes", (double)serialized_size);
                    cJSON_AddStringToObject(resp_json, "checksum", checksum);

                    char *json_str = cJSON_PrintUnformatted(resp_json);
                    cJSON_Delete(resp_json);
                    response = make_json_response(json_str, &http_status);
                    free(json_str);
                }
                cJSON_Delete(root);
            }
        }

    /* 404 para cualquier otra ruta */
    } else {
        http_status = MHD_HTTP_NOT_FOUND;
        const char *body = "{\"error\":\"Not found\"}";
        response = MHD_create_response_from_buffer(
            strlen(body), (void *)body, MHD_RESPMEM_PERSISTENT
        );
        MHD_add_response_header(response, "Content-Type", "application/json");
    }
 
    enum MHD_Result ret = MHD_queue_response(connection, http_status, response);
    MHD_destroy_response(response);
    return ret;
}
 
/* Limpieza del acumulador de body al finalizar la conexión */
static void request_completed(void *cls, struct MHD_Connection *connection,
                               void **con_cls, enum MHD_RequestTerminationCode toe)
{
    (void)cls; (void)connection; (void)toe;
    request_body_free((RequestBody *)*con_cls);
    *con_cls = NULL;
}
 
/* -------------------------------------------------------------------------
 * main
 * ---------------------------------------------------------------------- */
 
int main(void)
{
    /*
     * MHD_USE_THREAD_PER_CONNECTION  →  equivale al threadpool de uvicorn:
     * cada petición corre en su propio hilo, por lo que /hash-conc es
     * realmente paralela en núcleos distintos.
     */
    struct MHD_Daemon *daemon = MHD_start_daemon(
        MHD_USE_THREAD_PER_CONNECTION | MHD_USE_INTERNAL_POLLING_THREAD,
        PORT,
        NULL, NULL,
        &answer_to_connection, NULL,
        MHD_OPTION_NOTIFY_COMPLETED, &request_completed, NULL,
        MHD_OPTION_END
    );
 
    if (!daemon) {
        fprintf(stderr, "Error: no se pudo iniciar el servidor en el puerto %d\n", PORT);
        return 1;
    }
 
    printf("Benchmark Backend – C (multi-algoritmo)\n");
    printf("Escuchando en http://0.0.0.0:%d\n", PORT);
    printf("  POST /hash-seq        (secuencial)\n");
    printf("  POST /hash-conc       (concurrente)\n");
    printf("  POST /stringproc-seq  (secuencial)\n");
    printf("  POST /stringproc-conc (concurrente)\n");
    printf("  POST /prime-seq       (secuencial)\n");
    printf("  POST /prime-conc      (concurrente)\n");
    printf("  POST /jsonproc-seq    (secuencial)\n");
    printf("  POST /jsonproc-conc   (concurrente)\n");
    printf("  GET  /health\n");
    printf("Ctrl+C para detener.\n\n");
 
    /* Bloquear el hilo principal indefinidamente */
    pause();
 
    MHD_stop_daemon(daemon);
    return 0;
}