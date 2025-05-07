import logging
import string
import re
import random
import secrets
import uuid
import hashlib


import ckan.model as model
import ckan.plugins.toolkit as tk
from os import environ

log = logging.getLogger(__name__)

group_hierarchy = {
    'member': 1,
    'editor': 2,
    'admin': 3
}

def generate_password():
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(8))


def ensure_unique_username_from_email(email):
    localpart = email.split('@')[0]
    cleaned_localpart = re.sub(r'[^\w]', '-', localpart).lower()

    if not model.User.get(cleaned_localpart):
        return cleaned_localpart

    max_name_creation_attempts = 10

    for _ in range(max_name_creation_attempts):
        random_number = random.SystemRandom().random() * 10000
        name = '%s-%d' % (cleaned_localpart, random_number)
        if not model.User.get(name):
            return name

    return cleaned_localpart

def process_user(userinfo,sub_support):
    if sub_support:
        return _get_user_by_sub(userinfo.get('id'),userinfo) or _create_user(userinfo)
    else:
        return _get_user_by_email(userinfo.get('email')) or _create_user(userinfo)


def _get_user_by_email(email):
    user = model.User.by_email(email)
    if user and isinstance(user, list):
        user = user[0]

    activate_user_if_deleted(user)
    
    return user


def _get_user_by_sub(sub,userinfo):
    user = model.User.get(sub)

    if user and isinstance(user, list):
        user = user[0]

    user_email = user.email if user else None
    userinfo_email = userinfo.get('email') if userinfo else None

    if user_email != userinfo_email:
        if userinfo_email is not None and user_email is not None:
            log.info("Emails are different, update user data: {} != {}".format(userinfo_email, user_email))
            userinfo['name'] = ensure_unique_username_from_email(userinfo.get('email'))
            user = _patch_user({key: userinfo[key] for key in ['id', 'email', 'name']})
            log.info("Patched user email")
        else:
            log.warning("One of the emails is None. Cannot update.")

    activate_user_if_deleted(user)

    return user


def activate_user_if_deleted(user):
    u'''Reactivates deleted user.'''
    if not user:
        return
    if user.is_deleted():
        user.activate()
        user.commit()
        log.info(u'User {} reactivated'.format(user.name))

def handle_group_memberships(user_dict):
    tenants = {group.split('.')[0] for group in user_dict['groups']}
    tenant_ids = {hash_string_to_uuid(tenant): tenant for tenant in tenants}

    current_ckan_tenants = _current_orgs()
    current_ckan_tenants_id = {hash_string_to_uuid(tenant): tenant for tenant in current_ckan_tenants}

    tenant_ids = {
        tenant_id: tenant
        for tenant_id, tenant in tenant_ids.items()
        if tenant_id in current_ckan_tenants_id
    }    

    current_permissions = _permission_org_user({'id': user_dict['sub']})
    if current_permissions:
        permissions_dict = {permission['id']: permission['capacity'] for permission in current_permissions}
    else:
        permissions_dict = {}

    for tenant_id, tenant_name in tenant_ids.items():
        max_permission = get_highest_permission(user_dict['groups'], tenant_name)

        tenant_permission = permissions_dict.get(tenant_id, 0)

        if tenant_permission != max_permission:
            _org_member_create({
                "id": tenant_id,
                "username": user_dict['sub'],
                "role": max_permission
            })

    tenants_to_remove = [tenant_id for tenant_id in permissions_dict if tenant_id not in tenant_ids]

    for tenant_id in tenants_to_remove:
        tenant_name = permissions_dict.get(tenant_id, {}).get('name', 'Unbekannt')
        print(f"Berechtigung für {tenant_name} ({tenant_id}) wird entfernt, da der Benutzer nicht mehr in den Gruppen vorhanden ist.")
        _org_member_delete({
            "id": tenant_id,
            "user": user_dict['sub']
        })

    print("\nFinale Berechtigungen:")
    for tenant_id, capacity in permissions_dict.items():
        print(f"Tenant {tenant_id}: Berechtigung {capacity}")

def get_highest_permission(user_groups, tenant_prefix):
    tenant_groups = [group for group in user_groups if group.startswith(tenant_prefix)]

    if not tenant_groups:
        return None

    highest_group = max(
        tenant_groups,
        key=lambda group: group_hierarchy.get(group.split('.')[1].replace('ckan-', ''), 0)
    )

    highest_group_name = highest_group.split('.')[1].replace('ckan-', '')

    return highest_group_name

def _create_user(userinfo):
    context = {
        u'ignore_auth': True,
    }
    created_user_dict = tk.get_action(
        u'user_create'
    )(context, userinfo)
    
    return _get_user_by_email(created_user_dict['email'])

def _patch_user(userinfo):
    context = {
        u'ignore_auth': True,
    }
    updated_user_dict = tk.get_action(
        u'user_patch'
    )(context, userinfo)

    return _get_user_by_email(userinfo['email'])

def _permission_org_user(userinfo):
    context = {
    }
    response = tk.get_action(
        u'organization_list_for_user'
    )(context, userinfo)

    return response

def _org_member_create(userinfo):
    context = {
        u'ignore_auth': True,
    }
    response = tk.get_action(
        u'organization_member_create'
    )(context, userinfo)

    return response

def _current_orgs():
    return tk.get_action(u'organization_list')()

def _org_member_delete(userinfo):
    context = {
        u'ignore_auth': True,
    }
    response = tk.get_action(
        u'organization_member_delete'
    )(context, userinfo)

    return response

def button_style():

    return tk.config.get('ckanext.keycloak.button_style',
                         environ.get('CKANEXT__KEYCLOAK__BUTTON_STYLE'))


def enable_internal_login():

    return tk.asbool(tk.config.get(
        'ckanext.keycloak.enable_ckan_internal_login',
        environ.get('CKANEXT__KEYCLOAK__CKAN_INTERNAL_LOGIN')))

def hash_string_to_uuid(s: str) -> str:
    digest = hashlib.sha256()
    digest.update(s.encode('utf-8'))
    hash_bytes = digest.digest()
    return str(uuid.UUID(bytes=hash_bytes[:16]))