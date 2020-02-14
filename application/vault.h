#include <stdint.h>

#define VE_SUCCESS 0
#define VE_MEMERR 1
#define VE_PARAMERR 2
#define VE_IOERR 3
#define VE_CRYPTOERR 4
#define VE_VOPEN 5
#define VE_VCLOSE 6
#define VE_SYSCALL 7
#define VE_EXIST 8
#define VE_ACCESS 9
#define VE_KEYEXIST 10
#define VE_FILE 11
#define VE_NOSPACE 12

struct vault_info;

struct vault_info* init_vault();

int max_value_size();

int release_vault(struct vault_info* info);

int create_vault(char* directory, char* username, char* password, struct vault_info* info);

int create_from_header(char* directory, char* username, char* password, uint8_t* header, struct vault_info* info);

int open_vault(char* dreictory, char* username, char* password, struct vault_info* info);

int close_vault(struct vault_info* info);

int create_data_for_server(struct vault_info* info, uint8_t* response1, uint8_t* response2, uint8_t* password_salt,
                           uint8_t* recovery_result, uint8_t* dataencr1, uint8_t* dataencr2, uint8_t* data_salt_11,
                           uint8_t* data_salt_12, uint8_t* data_salt_21, uint8_t* data_salt_22, uint8_t* server_pass);

int create_password_for_server(struct vault_info* info, uint8_t* salt, uint8_t* server_pass);

int create_responses_for_server(uint8_t* response1, uint8_t* response2, uint8_t* data_salt_11, uint8_t* data_salt_12,
                                uint8_t* data_salt_21, uint8_t* data_salt_22, uint8_t* dataencr1, uint8_t* dataencr2);

int update_key_from_recovery(struct vault_info* info, uint8_t* response1, uint8_t* response2, uint8_t* recovery, uint8_t* data_salt_1,
                             uint8_t* data_salt_2, uint8_t* new_salt, uint8_t* new_server_pass, uint8_t* new_header);

int add_key(struct vault_info* info, uint8_t type, const char* key, const char* vaule);

int get_vault_keys(struct vault_info* info, char** results);

uint32_t num_vault_keys(struct vault_info* info);

uint64_t last_modified_time(struct vault_info* info, const char* key);

int open_key(struct vault_info* info, const char* key);

int delete_key(struct vault_info* info, const char* key);

int update_key(struct vault_info* info, uint8_t type, const char* key, const char* vaule);

int change_password(struct vault_info* info, const char* old_password, const char* new_password);

int place_open_value(struct vault_info*, char*, int*, char*);

int add_encrypted_value(struct vault_info* info, const char* key, const char* value, int len, uint8_t type);

int get_encrypted_value(struct vault_info* info, const char* key, char* result, int* len, uint8_t* type);

int get_header(struct vault_info* info, char* result);
