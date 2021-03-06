"""Main password bank application

This module creates and defines a main Bank object that interacts with
the rest of the application, acting as the entrypoint to the application
"""
import asyncio
from collections import defaultdict
import json
import itertools
import queue
import struct
import sys
import threading
import time
from typing import Tuple
from urllib.parse import urlparse
import os
import sys
from abc import *
from base64 import *

import janus

from application.utils import *
import application.vault as vault
from chrome_extension.bank_server import BankServer
import requests


class Bank():

    @staticmethod
    def decode_credentials(credentials: bytes) -> Tuple[str]:
        user_len = struct.unpack('i', credentials[0:4])[0]
        username = credentials[4:4 + user_len].decode('utf-8')
        password = credentials[4 + user_len:].decode('utf-8')
        return (username, password)

    @staticmethod
    def encode_credentials(username: str, password: str) -> bytes:
        value = struct.pack('i', len(username))
        value += username.encode()
        value += password.encode()
        return value

    def __init__(self):
        self._vault = vault.Vault()
        self.vault_lock = threading.Lock()
        self.logged_in = False
        self.initialize_vault_dir()
        self.cur_user = None
        self.cur_changes = defaultdict(lambda: (None, -1))
        self.bank_server = BankServer(6969)
        self.clipboard_queue = queue.Queue()
        self.bank_started = False
        self.salt1, self.salt2 = None, None

        self.start_threads()

    def start_threads(self):
        self.start_clipboard()
        self.start_bank_server()
        self.start_server_updater()

    def initialize_vault_dir(self):
        if os.path.isdir('vault'):
            return
        try:
            os.remove('vault')
        except Exception as e:
            print(f'initialize_vault_dir Error "{e}" of type {type(e)}',
                  file=sys.stderr,
                  flush=True)
        os.mkdir('vault')

    # Clipboard thread
    def start_clipboard(self):
        threading.Thread(None,
                         self._clipboard_bg_process,
                         args=(self.clipboard_queue,),
                         daemon=True).start()

    def _clipboard_bg_process(self, item_q: queue.Queue):
        last_item = time.time()
        while True:
            if time.time() - last_item > 30:
                clear_clipboard()
            try:
                item = item_q.get(block=False)
                last_item = time.time()
                copy_clipboard(item)
            except queue.Empty:
                time.sleep(0.1)

    # UI functionality

    def check_username(self, username):
        return self.get_salts(username)

    def sign_up(self, username, password, recovery1, recovery2):
        if not self.create_and_open(username, password):
            print('Failed to create file', file=sys.stderr)
            return False
        if not self.create_user(recovery1, recovery2):
            print('User exists in cloud', file=sys.stderr)
            self.close_user_file()
            return False
        self.get_salts(username)
        self.logged_in = True
        return True

    def get_recovery_questions(self, username):
        questions_response = requests.post(
            'https://noodlespasswordvault.com/recovery_questions',
            json={'username': username},
            verify=True)
        if questions_response.status_code != 200:
            return False
        return questions_response.json()['q1'], questions_response.json()['q2']

    def forgot_password(self, username, new_pass, response1, response2):
        questions_response = requests.post(
            'https://noodlespasswordvault.com/recovery_questions',
            json={'username': username},
            verify=True)

        if questions_response.status_code != 200:
            return False

        q_json = questions_response.json()

        d_salt11, d_salt12 = b64decode(q_json['data_salt_11']), b64decode(
            q_json['data_salt_12'])
        d_salt21, d_salt22 = b64decode(q_json['data_salt_21']), b64decode(
            q_json['data_salt_22'])

        self.vault_lock.acquire()
        resp1, resp2 = self._vault.create_responses_for_server(
            response1, response2, d_salt11, d_salt12, d_salt21, d_salt22)
        self.vault_lock.release()

        recovery_response = requests.post(
            'https://noodlespasswordvault.com/recover',
            json={
                'username': username,
                'r1': b64encode(resp1).decode('ascii'),
                'r2': b64encode(resp2).decode('ascii')
            },
            verify=True)

        if recovery_response.status_code != 200:
            return False

        vault_resp = None
        try:
            self.vault_lock.acquire()
            vault_resp = self._vault.update_key_from_recovery(
                'vault', username, response1, response2,
                b64decode(recovery_response.json()['recovery_key']), d_salt11,
                d_salt21, new_pass)
        except Exception as e:
            self.vault_lock.release()
            print(f'forgot_password Error "{e}" of type {type(e)}',
                  file=sys.stderr,
                  flush=True)
            return False
        self.vault_lock.release()
        recover_change = requests.post(
            'https://noodlespasswordvault.com/recovery_change',
            json={
                'username':
                    username,
                'recovery_1':
                    b64encode(resp1).decode('ascii'),
                'recovery_2':
                    b64encode(resp2).decode('ascii'),
                'new_password':
                    b64encode(vault_resp['password']).decode('ascii'),
                'new_salt_1':
                    b64encode(vault_resp['pass_salt_1']).decode('ascii'),
                'new_salt_2':
                    b64encode(vault_resp['pass_salt_2']).decode('ascii'),
                'new_master':
                    b64encode(vault_resp['recovery_key']).decode('ascii')
            },
            verify=True)
        if recover_change.status_code != 200:
            return False
        return True

    def check_user_exist(self, username):
        return os.path.isfile(f'vault/{username}.vault')

    def log_in(self, username, password):
        if username == '':
            return False
        if not self.open_user_file(username, password):
            return False
        self.logged_in = True
        return self.get_salts(username)

    def log_out(self):
        self.server_update()
        self.logged_in = False
        return self.close_user_file()

    def get_websites(self):
        return self.get_keys()

    def get_login_info(self, website):
        return self.get_credentials(website)

    def add_login_info(self, website, username, password):
        return self.add_credential(website, username, password)

    def delete_login_info(self, website):
        return self.delete_credential(website)

    # CHROME communication

    def start_bank_server(self):
        threading.Thread(None, self.run_bank_server, daemon=True).start()
        threading.Thread(None, self.listen_bank_server, daemon=True).start()

    def run_bank_server(self):
        self.bank_started = True
        asyncio.set_event_loop(asyncio.new_event_loop())
        loop = asyncio.get_event_loop()

        loop.run_until_complete(self.bank_server.run_server_forever())

    def listen_bank_server(self):
        while not self.bank_started:
            continue
        while True:
            self.bank_server.clients_lock.acquire()
            for cli in self.bank_server.clients:
                q = self.bank_server.client_messages[cli]
                if q.sync_q:
                    msg = q.sync_q.get()
                    print(f'{cli} sent {msg}', file=sys.stderr, flush=True)
                    netloc = urlparse(json.loads(msg)['url']).netloc
                    try:
                        username, password = self.get_credentials(netloc)
                    except Exception as e:
                        print(
                            f'listen_bank_error Error "{e}" of type {type(e)}',
                            file=sys.stderr,
                            flush=True)
                        print(f'Could not find value for key={netloc}',
                              file=sys.stderr,
                              flush=True)
                        continue

                    if username != None:
                        load = json.dumps({
                            'username': username,
                            'password': password
                        }).encode('ascii')
                        print(f'Sending back {load}',
                              file=sys.stderr,
                              flush=True)
                        self.bank_server.bank_messages[cli].sync_q.put(load)
                    else:
                        print(
                            f'Got back invalid value for key={netloc} - what?',
                            file=sys.stderr,
                            flush=True)
                    self.bank_server.bank_messages[cli].sync_q.put(None)

            self.bank_server.clients_lock.release()
            time.sleep(0.1)

    # AWS functionality

    def start_server_updater(self):
        threading.Thread(None, self.server_updater, daemon=True).start()

    def server_updater(self):
        while True:
            if self.logged_in:
                try:
                    self.vault_lock.acquire()
                    ctime = self._vault.get_last_contact_time()
                    self.vault_lock.release()
                    if get_time() - ctime > 60 * 1:
                        self.server_update()
                except Exception as e:
                    try:
                        self.vault_lock.release()
                    except RuntimeError:
                        pass
                    print(f'server_updater Error "{e}" of type {type(e)}',
                          file=sys.stderr,
                          flush=True)
                    pass
                time.sleep(1)

    def create_user(self, recovery1, recovery2):
        # recovery is a (question, answer) string tuple
        self.vault_lock.acquire()
        reg_json = self._vault.create_data_for_server(recovery1[1],
                                                      recovery2[1])
        self.vault_lock.release()
        # currently ignorning error code
        # will take a long time so maybe give UI indication

        self.vault_lock.acquire()
        reg_json['encrypted_master'] = self._vault.get_vault_header()
        self.vault_lock.release()

        for key in reg_json.keys():
            reg_json[key] = b64encode(reg_json[key]).decode('ascii')

        reg_json['q1'] = recovery1[0]
        reg_json['q2'] = recovery2[0]
        reg_json['username'] = self.cur_user

        reg_resp = requests.post('https://noodlespasswordvault.com/register',
                                 json=reg_json,
                                 verify=True)

        if reg_resp.status_code != 200:
            return False
        self.vault_lock.acquire()
        self._vault.set_last_contact_time(int(reg_resp.json()['time']))
        self.vault_lock.release()
        return True

    def get_salts(self, username):
        salt_request = requests.post('https://noodlespasswordvault.com/salt',
                                     json={'username': username},
                                     verify=True)
        if salt_request.status_code != 200:
            return False
        self.salt1 = b64decode(
            salt_request.json()['pass_salt_1'].encode('ascii'))
        self.salt2 = b64decode(
            salt_request.json()['pass_salt_2'].encode('ascii'))
        return True

    def server_update(self):
        # pull server updates
        self.vault_lock.acquire()
        encoded_pass = b64encode(
            self._vault.create_password_for_server(self.salt2)).decode('ascii')
        self.vault_lock.release()

        check_json = {
            'username': self.cur_user,
            'password': encoded_pass,
            'last_update_time': self._vault.get_last_contact_time()
        }  # TIME
        check_resp = requests.post('https://noodlespasswordvault.com/check',
                                   json=check_json,
                                   verify=True)

        if check_resp.status_code != 200:
            return False

        server_changes = defaultdict(lambda: (None, -1),
                                     check_resp.json()['updates'])
        server_updates = {}
        local_updates = {}

        print(f'server_changes: {server_changes}', file=sys.stderr, flush=True)
        print(f'cur_changes: {self.cur_changes}', file=sys.stderr, flush=True)

        for key in set(
                itertools.chain(server_changes.keys(),
                                self.cur_changes.keys())):
            if server_changes[key][1] > self.cur_changes[key][1]:
                local_updates[key] = server_changes[key]
            else:
                server_updates[key] = self.cur_changes[key]

        for site, (new_creds, _time) in local_updates.items():
            try:
                self.vault_lock.acquire()
                self._vault.delete_value(site)
            except:
                pass
            if new_creds != None:
                self._vault.add_encrypted_value(0, site, b64decode(new_creds),
                                                _time)
            self.vault_lock.release()

        for site in server_updates.keys():
            value, time = server_updates[site]
            if value != None:
                newval = b64encode(value).decode('ascii')
                server_updates[site] = (newval, time)

        update_resp = requests.post(
            'https://noodlespasswordvault.com/update',
            json={
                'username': self.cur_user,
                'password': encoded_pass,
                # 'last_updated_time': 0,
                'updates': server_updates
            },
            verify=True)
        self.cur_changes.clear()

        check2_json = {
            'username': self.cur_user,
            'password': encoded_pass,
            'last_update_time': check_resp.json()['time']
        }  # TIME
        check2_resp = requests.post('https://noodlespasswordvault.com/check',
                                    json=check2_json,
                                    verify=True)

        server_changes = check2_resp.json()['updates']

        for site, (new_creds, _time) in server_changes.items():
            if (new_creds, _time) != server_updates[site]:
                print(
                    f'Changing because server_updates[{site}] == {server_updates[site]} != server_changes[{site}] == {server_changes[site]}'
                )
                self.modify_credential(site,
                                       *Bank.decode_credentials(new_creds))

        self.vault_lock.acquire()
        self._vault.set_last_contact_time(int(check2_resp.json()['time']))
        self.vault_lock.release()
        # check each against local copy to see which is newer
        # if local newer,

    def download_vault(self, username, password):
        try:
            salts = self.get_salts(username)
            self.vault_lock.acquire()
            server_pass = self._vault.make_password_for_server(
                password, self.salt1, self.salt2)
        except:
            self.vault_lock.release()
            return "Internal Vault Error"
        self.vault_lock.release()

        download_json = {
            'username': username,
            'password': b64encode(server_pass).decode('ascii')
        }
        download_resp = requests.post(
            'https://noodlespasswordvault.com/download',
            json=download_json,
            verify=True)

        if download_resp.status_code == 200:
            header = b64decode(download_resp.json()['header'].encode('ascii'))
            keys = download_resp.json()['pairs']
            c_time = int(download_resp.json()['time'])
            for_server = []
            for key, values in keys.items():
                for_server.append((key, 0, b64decode(values[0]), values[1]))
            self.vault_lock.acquire()
            self._vault.create_vault_from_server_data('vault', username,
                                                      password, header,
                                                      for_server)
            self.cur_user = username
            self.logged_in = True
            self._vault.set_last_contact_time(c_time)
            self.vault_lock.release()
            return None
        else:
            try:
                return download_resp.json()['error']
            except:
                return "Internal Error"

    # Chrome Extension functionality
    # should now open tcp listening server

    # C Vault functionality

    def create_and_open(self, username, password):
        try:
            self.vault_lock.acquire()
            self._vault.create_vault('vault', username, password)
        except Exception as e:
            self.vault_lock.release()
            print(f'create_and_open Error "{e}" of type {type(e)}',
                  file=sys.stderr,
                  flush=True)
            return False
        self.vault_lock.release()
        self.cur_user = username
        return True

    def open_user_file(self, username, password):
        try:
            self.vault_lock.acquire()
            self._vault.open_vault('vault', username, password)
        except Exception as e:
            self.vault_lock.release()
            print(f'open_user_file Error "{e}" of type {type(e)}',
                  file=sys.stderr,
                  flush=True)
            return False
        self.vault_lock.release()
        self.cur_user = username
        return True

    def close_user_file(self):
        try:
            self.vault_lock.acquire()
            self._vault.close_vault()
        except Exception as e:
            self.vault_lock.release()
            print(f'close_user_file Error "{e}" of type {type(e)}',
                  file=sys.stderr,
                  flush=True)
            return False
        self.vault_lock.release()
        self.cur_user = None
        return True

    def add_credential(self, website, username, password):
        cur_time = get_time()
        try:
            self.vault_lock.acquire()
            self._vault.add_key(0, website,
                                Bank.encode_credentials(username, password),
                                cur_time)
        except Exception as e:
            self.vault_lock.release()
            print(f'add_credential Error "{e}" of type {type(e)}',
                  file=sys.stderr,
                  flush=True)
            return False
        self.cur_changes[website] = (
            self._vault.get_encrypted_value(website)[1], cur_time)
        self.vault_lock.release()
        return True

    def modify_credential(self, website, username, new_password):
        cur_time = get_time()
        try:
            self.vault_lock.acquire()
            self._vault.update_value(
                0, website, Bank.encode_credentials(username, new_password),
                cur_time)
        except Exception as e:
            self.vault_lock.release()
            print(f'modify_credential Error "{e}" of type {type(e)}',
                  file=sys.stderr,
                  flush=True)
            return False
        self.cur_changes[website] = (
            self._vault.get_encrypted_value(website)[1], cur_time)
        self.vault_lock.release()
        return True

    def delete_credential(self, website):
        cur_time = get_time()
        try:
            self.vault_lock.acquire()
            self._vault.delete_value(website)
        except Exception as e:
            self.vault_lock.release()
            print(f'delete_credential Error "{e}" of type {type(e)}',
                  file=sys.stderr,
                  flush=True)
            return False
        self.vault_lock.release()
        self.cur_changes[website] = (None, cur_time)
        return True

    def get_credentials(self, website):
        try:
            self.vault_lock.acquire()
            key_type, data = self._vault.get_value(website)
        except Exception as e:
            self.vault_lock.release()
            print(f'get_credential Error "{e}" of type {type(e)}',
                  file=sys.stderr,
                  flush=True)
            return (None, None)
        self.vault_lock.release()
        if key_type == 1:
            return (data,)
        elif key_type == 0:
            return Bank.decode_credentials(data)
        return (None, None)

    def get_keys(self):
        self.vault_lock.acquire()
        v_keys = self._vault.get_vault_keys()
        self.vault_lock.release()
        return v_keys


if __name__ == '__main__':
    b = Bank()

    try:
        b.create_and_open('user', 'pass')
    except vault.VaultExistsException:
        try:
            b.log_in('user', 'pass')
        except:
            print('Issue logging into test user', file=sys.stderr, flush=True)
            while True:
                continue

    try:
        b.get_credentials('stackoverflow.com')
    except vault.KeyException:
        b.add_credential('stackoverflow.com', 'testuser', 'testpass')
        pass

    while True:
        continue
