#!/usr/bin/env python
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

import argparse
import time
import datetime
import subprocess
import signal
import sys
import shutil
import os
import uuid
import json
import yaml
import random
import math
import re
import requests
import urllib
import logging
import configparser
from packaging import version as ver
import threading
import concurrent.futures
from git import Repo
from libs import common
from libs import parentParsers


def set_force_terminate(signum, frame):
    logging.warning("Captured Ctrl-C, sending exit event to watcher, any cluster install/delete will continue its execution")
    global force_terminate
    force_terminate = True


def disable_signals():
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _verify_cmnds(ocm_cmnd, rosa_cmnd, my_path, ocm_version, rosa_version):
    if rosa_cmnd is None:
        logging.info('rosa command not provided')
        logging.info('Downloading binary rosa-linux-amd64 from https://github.com/openshift/rosa/releases/')
        tags_list = []
        try:
            tags = requests.get(url='https://api.github.com/repos/openshift/rosa/git/refs/tags')
        except requests.ConnectionError as err:
            logging.error('Cannot download tags list from https://api.github.com/repos/openshift/rosa/git/refs/tags')
            logging.error(err)
            exit(1)
        # Get all tags, sort and select the correct one
        for tag in tags.json():
            tags_list.append(tag['ref'].split('/')[-1].split('v')[-1])
        logging.debug('List of tags: %s' % tags_list)
        if rosa_version == 'latest':
            version = sorted(tags_list, key=ver.parse)[-1]
        else:
            version = None
            for tag in tags_list:
                if tag == rosa_version:
                    version = tag
            if version is None:
                version = sorted(tags_list, key=ver.parse)[-1]
                logging.error('Invalid ROSA release %s, downloading latest release identified as %s' % (rosa_version, version))
        logging.info('Downloading ROSA release identified as %s' % version)
        try:
            url = 'https://github.com/openshift/rosa/releases/download/v' + version + '/rosa-linux-amd64'
            logging.debug('Downloading from %s' % url)
            with urllib.request.urlopen(url) as response, open(my_path + '/rosa', 'wb') as out_file:
                shutil.copyfileobj(response, out_file)
            os.chmod(my_path + '/rosa', 0o777)
            rosa_cmnd = my_path + "/rosa"
        except urllib.error.HTTPError as err:
            logging.error('Failed to download valid version %s from GitHub: %s' % (version, err))
            exit(1)
    logging.info('Testing rosa command with: rosa -h')
    rosa_cmd = [rosa_cmnd, "-h"]
    rosa_process = subprocess.Popen(rosa_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    rosa_stdout, rosa_stderr = rosa_process.communicate()
    if rosa_process.returncode != 0:
        logging.error('%s unable to execute -h' % rosa_cmnd)
        logging.error(rosa_stderr.strip().decode("utf-8"))
        exit(1)
    logging.info('rosa command validated with -h. Directory is %s' % my_path)
    if ocm_cmnd is None:
        logging.info('ocm command not provided')
        logging.info('Downloading binary ocm from https://github.com/openshift-online/ocm-cli/releases/')
        tags_list = []
        try:
            tags = requests.get(url='https://api.github.com/repos/openshift-online/ocm-cli/git/refs/tags')
        except (requests.ConnectionError, urllib.error.HTTPError) as err:
            logging.error('Cannot download tags list from https://api.github.com/repos/openshift-online/ocm-cli/git/refs/tags')
            logging.error(err)
            exit(1)
        # Get all tags, sort and select the correct one
        for tag in tags.json():
            tags_list.append(tag['ref'].split('/')[-1].split('v')[-1])
        logging.debug('List of tags: %s' % tags_list)
        if ocm_version == 'latest':
            version = sorted(tags_list, key=ver.parse)[-1]
        else:
            version = None
            for tag in tags_list:
                if tag == ocm_version:
                    version = tag
            if version is None:
                version = sorted(tags_list, key=ver.parse)[-1]
                logging.error('Invalid OCM release %s, downloading latest release identified as %s' % (ocm_version, version))
        logging.info('Downloading OCM release identified as %s' % version)
        try:
            url = 'https://github.com/openshift-online/ocm-cli/releases/download/v' + version + '/ocm-linux-amd64'
            with urllib.request.urlopen(url) as response, open(my_path + '/ocm', 'wb') as out_file:
                shutil.copyfileobj(response, out_file)
            os.chmod(my_path + '/ocm', 0o777)
            ocm_cmnd = my_path + "/ocm"
        except urllib.error.HTTPError as err:
            logging.error('Failed to download valid version %s from GitHub: %s' % (version, err))
            exit(1)
    logging.info('Testing ocm command with: ocm -h')
    ocm_cmd = [ocm_cmnd, "-h"]
    ocm_process = subprocess.Popen(ocm_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ocm_stdout, ocm_stderr = ocm_process.communicate()
    if ocm_process.returncode != 0:
        logging.error('%s unable to execute -h' % ocm_cmnd)
        logging.error(ocm_stderr.strip().decode("utf-8"))
        exit(1)
    logging.info('ocm command validated with -h. Directory is %s' % my_path)
    return (ocm_cmnd, rosa_cmnd)


def _gen_oidc_config_id(rosa_cmnd, cluster_name_seed, my_path):
    logging.info('Creating OIDC Provider')
    oidc_gen_cmd = [rosa_cmnd, 'create', 'oidc-config', '--mode=auto', '--managed=false', '--prefix', cluster_name_seed, '-y']
    logging.debug(oidc_gen_cmd)
    oidc_gen_log = open(my_path + "/" + 'oidc_config_id_gen.log', 'w')
    oidc_gen_process = subprocess.Popen(oidc_gen_cmd, stdout=oidc_gen_log, stderr=oidc_gen_log)
    oidc_gen_stdout, oidc_gen_stderr = oidc_gen_process.communicate()
    if oidc_gen_process.returncode != 0:
        logging.error('Unable to generate OIDC Provider. Please check logfile %s' % my_path + "/" + 'oidc_config_id_gen.log')
        exit(1)
    # the rosa cli output does not give us the OIDC Provider ID so we need to scrape it after
    oidc_get_cmd = [rosa_cmnd, 'list', 'oidc-config', '-o', 'json']
    oidc_get_process = subprocess.Popen(oidc_get_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    oidc_get_stdout, oidc_get_stderr = oidc_get_process.communicate()
    if oidc_get_process.returncode != 0:
        logging.error('rosa list oidc-config -o json command returned an error. Exiting...')
        logging.error(oidc_get_stderr.strip().decode("utf-8"))
        exit(1)
    for oidc_item in json.loads(oidc_get_stdout.decode("utf-8")):
        if cluster_name_seed in oidc_item['issuer_url']:
            logging.info('OIDC Provider found. ID is %s' % oidc_item['id'])
            return oidc_item['id']
    logging.error('OIDC ID not found in rosa list oidc-config for cluster name seed %s' % cluster_name_seed)
    logging.error(oidc_get_stdout.strip().decode("utf-8"))
    exit(1)


def _verify_oidc_config_id(oidc_config_id, rosa_cmnd, my_path):
    logging.info('Verifying %s is in list of OIDC Providers' % oidc_config_id)
    oidc_verify_cmd = [rosa_cmnd, 'list', 'oidc-config', '-o', 'json']
    oidc_verify_process = subprocess.Popen(oidc_verify_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    oidc_verify_stdout, oidc_verify_stderr = oidc_verify_process.communicate()
    if oidc_verify_process.returncode != 0:
        logging.error('rosa list oidc-config -o json command returned an error. Exiting...')
        return False
    for oidc_id in json.loads(oidc_verify_stdout.decode("utf-8")):
        if oidc_id['id'] == oidc_config_id:
            logging.info('Found OIDC ID %s' % oidc_config_id)
            return True
    logging.error('OIDC ID %s not found in rosa list oidc-config' % oidc_config_id)
    logging.error(oidc_verify_stdout.strip().decode("utf-8"))
    return False


def _verify_provision_shard(ocm_cmnd, shard_id):
    logging.info('Verifing Shard ID: %s' % shard_id)
    ocm_command = [ocm_cmnd, "get", "/api/clusters_mgmt/v1/provision_shards/" + shard_id]
    logging.debug(ocm_command)
    ocm_process = subprocess.Popen(ocm_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ocm_stdout, ocm_stderr = ocm_process.communicate()
    if ocm_process.returncode != 0:
        logging.error(ocm_stdout.strip().decode("utf-8"))
        logging.error(ocm_stderr.strip().decode("utf-8"))
        logging.error('%s unable to execute. Exiting...' % ocm_command)
        exit(1)
    else:
        if json.loads(ocm_stdout.decode("utf-8")).get('hypershift_config', {}).get('server', {}) and json.loads(ocm_stdout.decode("utf-8")).get('status', {}) in ('ready', 'maintenance'):
            # hypershift_config.server is the service cluster, like https: // api.hs-sc-0vfs0cl5g.wqrn.s1.devshift.org: 6443. split('.')[1] will return hs-sc-0vfs0cl5g
            logging.info("Identified Service Cluster %s for Shard ID %s" % (json.loads(ocm_stdout.decode("utf-8"))['hypershift_config']['server'].split('.')[1], shard_id))
            return json.loads(ocm_stdout.decode("utf-8"))['hypershift_config']['server'].split('.')[1]
        else:
            logging.error(ocm_stdout.strip().decode("utf-8"))
            logging.error(ocm_stderr.strip().decode("utf-8"))
            logging.error('Invalid Provision Shard %s. Exiting...' % shard_id)
            exit(1)


def _get_mgmt_cluster(sc_kubeconfig, cluster_id, cluster_name):
    starting_time = datetime.datetime.utcnow().timestamp()
    logging.info('Getting Management Cluster assigned for %s' % cluster_name)
    myenv = os.environ.copy()
    myenv["KUBECONFIG"] = sc_kubeconfig
    oc_cmnd = ["oc", "get", "managedclusters", cluster_id, "-o", "json"]
    logging.debug(oc_cmnd)
    logging.info("Waiting 15 minutes until %s for Management Cluster to be assigned to Hosted Cluster %s" % (cluster_name, datetime.datetime.fromtimestamp(starting_time + 15 * 60)))
    while datetime.datetime.utcnow().timestamp() < starting_time + 15 * 60:
        oc_process = subprocess.Popen(oc_cmnd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=myenv)
        oc_stdout, oc_stderr = oc_process.communicate()
        if oc_process.returncode != 0:
            logging.error(oc_stdout.strip().decode("utf-8"))
            logging.error(oc_stderr.strip().decode("utf-8"))
            logging.error('%s unable to execute. Retrying in 5 seconds until %s' % (oc_cmnd, datetime.datetime.fromtimestamp(starting_time + 15 * 60)))
            time.sleep(5)
        else:
            try:
                hostedcluster_json = json.loads(oc_stdout)
            except Exception as err:
                logging.warning(oc_stdout)
                logging.warning(oc_stderr)
                logging.warning(err)
                logging.warning("Failed to get the hosted cluster output for %s Cluster. Retrying in 5 seconds until %s" % (cluster_name, datetime.datetime.fromtimestamp(starting_time + 15 * 60)))
                time.sleep(5)
                continue
            if hostedcluster_json.get('metadata', {}).get('labels', {}).get('api.openshift.com/management-cluster'):

                return hostedcluster_json['metadata']['labels']['api.openshift.com/management-cluster']
            else:
                logging.warning("Failed to get the Management Cluster assigned for Hosted Cluster %s. Retrying in 5 seconds until %s" % (cluster_name, datetime.datetime.fromtimestamp(starting_time + 15 * 60)))
                time.sleep(5)
    logging.error('No Management Cluster assigned to %s after 15 minutes' % cluster_name)
    return 1


def _gen_operator_roles(rosa_cmnd, cluster_name_seed, my_path, oidc_id, installer_role_arn):
    logging.info("Creating Operator Roles")
    roles_cmd = [rosa_cmnd, 'create', 'operator-roles', '--prefix', cluster_name_seed, '-m', 'auto', '-y', '--hosted-cp', '--oidc-config-id', oidc_id, '--installer-role-arn', installer_role_arn]
    logging.debug(roles_cmd)
    roles_log = open(my_path + "/" + 'rosa_create_operator_roles.log', 'w')
    roles_process = subprocess.Popen(roles_cmd, stdout=roles_log, stderr=roles_log)
    roles_stdout, roles_stderr = roles_process.communicate()
    if roles_process.returncode != 0:
        logging.error('Unable to create operator roles. Please check logfile %s' % my_path + "/" + 'rosa_create_operator_roles.log')
        exit(1)
    else:
        return True


def _delete_operator_roles(rosa_cmnd, cluster_name_seed, my_path):
    logging.info("Deleting Operator Roles with prefix: %s" % cluster_name_seed)
    roles_cmd = [rosa_cmnd, 'delete', 'operator-roles', '--prefix', cluster_name_seed, '-m', 'auto', '-y']
    logging.debug(roles_cmd)
    roles_log = open(my_path + "/" + 'rosa_delete_operator_roles.log', 'w')
    roles_process = subprocess.Popen(roles_cmd, stdout=roles_log, stderr=roles_log)
    roles_stdout, roles_stderr = roles_process.communicate()
    if roles_process.returncode != 0:
        logging.error('Unable to delete operator roles. Please manually delete them using `rosa delete operator-roles --prefix %s -m auto -y and check logfile %s for errors' % (cluster_name_seed, my_path + "/" + 'rosa_create_operator_roles.log'))
        return False
    else:
        return True


def _find_installer_role_arn(rosa_cmnd, my_path):
    logging.info("Find latest Installer Role ARN")
    roles_cmd = [rosa_cmnd, 'list', 'account-roles', '-o', 'json']
    logging.debug(roles_cmd)
    roles_process = subprocess.Popen(roles_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    roles_stdout, roles_stderr = roles_process.communicate()
    if roles_process.returncode != 0:
        logging.error('Unable to list account Roles. Please check output result of `rosa list account-roles command`')
        logging.error(roles_stdout)
        logging.error(roles_stderr)
        exit(1)
    else:
        installer_role_version = ver.parse("0")
        installer_role_arn = None
        for role in json.loads(roles_stdout.decode("utf-8")):
            if role['RoleType'] == "Installer" and ver.parse(role['Version']) > installer_role_version:
                installer_role_arn = role['RoleARN']
                installer_role_version = ver.parse(role['Version'])
        return installer_role_arn


def _verify_terraform(terraform_cmnd, my_path):
    logging.info('Testing terraform command with: terraform -version')
    terraform_cmd = [terraform_cmnd, "-version"]
    terraform_log = open(my_path + "/" + 'terraform-version.log', 'w')
    terraform_process = subprocess.Popen(terraform_cmd, stdout=terraform_log, stderr=terraform_log)
    terraform_stdout, terraform_stderr = terraform_process.communicate()
    if terraform_process.returncode != 0:
        logging.error('%s unable to execute -version' % terraform_cmnd)
        exit(1)
    logging.info('terraform command validated with -version. Directory is %s' % my_path)
    return terraform_cmnd


def _create_vpcs(terraform, retries, my_path, cluster_name_seed, cluster_count, aws_region):
    logging.info('Initializing Terraform with: terraform init')
    terraform_init = [terraform, "init"]
    terraform_log = open(my_path + "/terraform/" + 'terraform-apply.log', 'w')
    terraform_init_process = subprocess.Popen(terraform_init, cwd=my_path + "/terraform", stdout=terraform_log, stderr=terraform_log)
    terraform_init_stdout, terraform_init_stderr = terraform_init_process.communicate()
    if terraform_init_process.returncode != 0:
        logging.error('Failed to initialize terraform on %s' % my_path + "/terraform")
        return 1
    logging.info('Applying terraform plan command with: terraform apply for %s VPC(s), using %s as name seed on %s' % (cluster_count, cluster_name_seed, aws_region))
    for trying in range(1, retries + 1):
        logging.info('Try: %d. Starting terraform apply' % trying)
        myenv = os.environ.copy()
        myenv["TF_VAR_cluster_name_seed"] = cluster_name_seed
        myenv["TF_VAR_cluster_count"] = str(cluster_count)
        myenv["TF_VAR_aws_region"] = aws_region
        terraform_apply = [terraform, "apply", "--auto-approve"]
        terraform_apply_process = subprocess.Popen(terraform_apply, cwd=my_path + "/terraform", stdout=terraform_log, stderr=terraform_log, env=myenv)
        terraform_apply_stdout, terraform_apply_stderr = terraform_apply_process.communicate()
        if terraform_apply_process.returncode == 0:
            logging.info('Applied terraform plan command with: terraform apply')
            try:
                with open(my_path + "/terraform/terraform.tfstate", "r") as terraform_file:
                    json_output = json.load(terraform_file)
            except Exception as err:
                logging.error(err)
                logging.error('Try: %d. Failed to read terraform output file %s' % (trying, my_path + "/terraform/terraform.tfstate"))
                return 1
            vpcs = []
            # Check if we have IDs for everything
            number_of_vpcs = len(json_output['outputs']['vpc-id']['value'])
            number_of_public = len(json_output['outputs']['cluster-public-subnets']['value'])
            number_of_private = len(json_output['outputs']['cluster-private-subnets']['value'])
            if number_of_vpcs != cluster_count or number_of_public != cluster_count or number_of_private != cluster_count:
                logging.info("Required Clusters: %d" % cluster_count)
                logging.info('Number of VPCs: %d' % number_of_vpcs)
                logging.info('Number of Private Subnets: %d' % number_of_private)
                logging.info('Number of Public Subnets: %d' % number_of_public)
                logging.warning('Try %d: Not all resources has been created. retring in 15 seconds' % trying)
                time.sleep(15)
            else:
                for cluster in range(cluster_count):
                    vpc_id = json_output['outputs']['vpc-id']['value'][cluster]
                    public_subnets = json_output['outputs']['cluster-public-subnets']['value'][cluster]
                    private_subnets = json_output['outputs']['cluster-private-subnets']['value'][cluster]
                    if len(public_subnets) != 3 or len(private_subnets) != 3:
                        logging.warning("Try: %d. Number of public subnets of VPC %s: %d (required: 3)" % (trying, vpc_id, len(public_subnets)))
                        logging.warning("Try: %d. Number of private subnets of VPC %s: %d (required: 3)" % (trying, vpc_id, len(private_subnets)))
                        logging.warning("Try: %d: Not all subnets created, retring in 15 seconds" % trying)
                        time.sleep(15)
                    else:
                        logging.debug("VPC ID: %s, Public Subnet: %s, Private Subnet: %s" % (vpc_id, public_subnets, private_subnets))
                        subnets = ",".join(public_subnets)
                        subnets = subnets + "," + ",".join(private_subnets)
                        vpcs.append((vpc_id, subnets))
                return vpcs
        else:
            logging.warning('Try: %d. %s unable to execute apply, retrying in 15 seconds' % (trying, terraform))
            time.sleep(15)
    logging.error('Failed to appy terraform plan after %d retries' % retries)
    return []


def _destroy_vpcs(terraform, retries, my_path, aws_region, vpcs):
    terraform_destroy = [terraform, "destroy", "--auto-approve"]
    terraform_log = open(my_path + "/terraform/" + 'terraform-destroy.log', 'w')
    for trying in range(1, retries + 1):
        if args.manually_cleanup_secgroups:
            for cluster in vpcs:
                logging.info("Try: %d. Starting manually destroy of security groups" % trying)
                _delete_security_groups(aws_region, my_path, cluster[0])
        logging.info("Try: %d. Starting terraform destroy process" % trying)
        terraform_destroy_process = subprocess.Popen(terraform_destroy, cwd=my_path + "/terraform", stdout=terraform_log, stderr=terraform_log)
        terraform_destroy_stdout, terraform_destroy_stderr = terraform_destroy_process.communicate()
        if terraform_destroy_process.returncode == 0:
            logging.info("Try: %d. All VPCs destroyed" % trying)
            return 0
        else:
            logging.error('Try: %d. Failed to execute %s destroy, retrying in 15 seconds' % (trying, terraform))
            time.sleep(15)
    return 1


def _delete_security_groups(aws_region, my_path, vpc_id):
    aws_log = open(my_path + "/terraform/" + 'aws_delete_sec_groups.log', 'w')
    sec_groups_command = ["aws", "ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=" + vpc_id, "Name=group-name,Values=default,k8s*", "--region=" + aws_region, "--output", "json"]
    logging.debug(sec_groups_command)
    sec_groups_process = subprocess.Popen(sec_groups_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sec_groups_stdout, sec_groups_stderr = sec_groups_process.communicate()
    if sec_groups_process.returncode == 0:
        for secgroup in json.loads(sec_groups_stdout.decode("utf-8"))['SecurityGroups']:
            logging.info("Security GroupID: %s" % secgroup['GroupId'])
            sec_groups_rules_command = ["aws", "ec2", "describe-security-group-rules", "--filters", "Name=group-id,Values=" + secgroup['GroupId'], "--region=" + aws_region, "--output", "json"]
            logging.debug(sec_groups_rules_command)
            sec_groups_rules_process = subprocess.Popen(sec_groups_rules_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            sec_groups_rules_stdout, sec_groups_rules_stderr = sec_groups_rules_process.communicate()
            if sec_groups_rules_process.returncode == 0:
                for secgrouprule in json.loads(sec_groups_rules_stdout.decode("utf-8"))['SecurityGroupRules']:
                    if secgroup['GroupName'] == 'default':
                        logging.info("Security Group Rule ID: %s of %s" % (secgrouprule['SecurityGroupRuleId'], secgroup['GroupId']))
                        sec_groups_rule_delete_command = ["aws", "ec2", "revoke-security-group-ingress", "--region=" + aws_region, "--group-id", secgroup['GroupId'], "--security-group-rule-ids", secgrouprule['SecurityGroupRuleId']]
                        logging.debug(sec_groups_rule_delete_command)
                        sec_groups_rule_delete_process = subprocess.Popen(sec_groups_rule_delete_command, stdout=aws_log, stderr=aws_log)
                        sec_groups_rule_delete_stdout, sec_groups_rule_delete_stderr = sec_groups_rule_delete_process.communicate()
                        if sec_groups_rule_delete_process.returncode != 0:
                            logging.error("Failed to revoke rule %s on Security Group %s" % (secgrouprule['SecurityGroupRuleId'], secgroup['GroupId']))
                            logging.error(sec_groups_rule_delete_stderr)
                            logging.error(sec_groups_rule_delete_stdout)
                        else:
                            logging.error("Revoked rule %s on Security Group %s" % (secgrouprule['SecurityGroupRuleId'], secgroup['GroupId']))
                    else:
                        logging.info("Deleting Security Group: %s" % secgroup['GroupName'])
                        sec_groups_delete_command = ["aws", "ec2", "delete-security-group", "--region=" + aws_region, "--group-id", secgroup['GroupId']]
                        logging.debug(sec_groups_delete_command)
                        sec_groups_delete_process = subprocess.Popen(sec_groups_delete_command, stdout=aws_log, stderr=aws_log)
                        sec_groups_delete_stdout, sec_groups_delete_stderr = sec_groups_delete_process.communicate()
                        if sec_groups_delete_process.returncode != 0:
                            logging.error("Failed to delete Security Group %s" % secgroup['GroupName'])
                            logging.error(sec_groups_delete_stdout)
                            logging.error(sec_groups_delete_stderr)
                        else:
                            logging.info("Deleted Security Group %s" % secgroup['GroupName'])
            else:
                logging.error("Failed to describe rules for Security Group %s" % secgroup['GroupId'])
                logging.error(sec_groups_rules_stderr)
                logging.error(sec_groups_rules_stdout)
                return 1
    else:
        logging.error("Failed to describe security groups")
        logging.error(sec_groups_stdout)
        logging.error(sec_groups_stderr)
        return 1


def _get_mgmt_cluster_info(ocm_cmnd, mgmt_cluster, es, index, index_retry, uuid):
    logging.info('Searching for Management/Service Clusters with name %s' % mgmt_cluster)
    ocm_command = [ocm_cmnd, "get", "/api/clusters_mgmt/v1/clusters?search=name+is+%27" + mgmt_cluster + "%27"]
    logging.debug(ocm_command)
    ocm_process = subprocess.Popen(ocm_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ocm_stdout, ocm_stderr = ocm_process.communicate()
    metadata = {}
    if ocm_process.returncode != 0:
        logging.error('%s unable to execute ' % ocm_command)
        logging.error(ocm_stderr.strip().decode("utf-8"))
    else:
        for cluster in json.loads(ocm_stdout.decode("utf-8"))['items']:
            if cluster['id'] == mgmt_cluster or cluster['name'] == mgmt_cluster:
                metadata['uuid'] = uuid
                metadata['cluster_name'] = cluster['name']
                metadata['infra_id'] = cluster['infra_id']
                metadata['cluster_id'] = cluster['id']
                metadata['version'] = cluster['openshift_version']
                metadata['base_domain'] = cluster['dns']['base_domain']
                metadata['aws_region'] = cluster['region']['id']
                if 'compute' in cluster['nodes']:
                    metadata['workers'] = cluster['nodes']['compute']
                else:  # when autoscaling enabled
                    metadata['workers'] = cluster['nodes']['autoscale_compute']['min_replicas']
                    metadata['workers_min'] = cluster['nodes']['autoscale_compute']['min_replicas']
                    metadata['workers_max'] = cluster['nodes']['autoscale_compute']['max_replicas']
                metadata['workers_type'] = cluster['nodes']['compute_machine_type']['id']
                metadata['network_type'] = cluster['network']['type']
                metadata["timestamp"] = datetime.datetime.utcnow().isoformat()
                metadata['install_method'] = "rosa"
                es_ignored_metadata = ""
                if es is not None:
                    common._index_result(es, index, metadata, es_ignored_metadata, index_retry)
        if metadata == {}:
            logging.error("Management/Service Cluster %s not found" % mgmt_cluster)
            exit(1)
    return metadata


def _download_kubeconfig(ocm_cmnd, cluster_id, my_path, type):
    logging.debug('Downloading kubeconfig file for Cluster %s on %s' % (cluster_id, my_path))
    kubeconfig_cmd = [ocm_cmnd, "get", "/api/clusters_mgmt/v1/clusters/" + cluster_id + "/credentials"]
    logging.debug(kubeconfig_cmd)
    process = subprocess.Popen(kubeconfig_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=my_path, universal_newlines=True)
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        logging.error('Failed to download kubeconfig file for Cluster ID %s with this stdout/stderr:' % cluster_id)
        logging.error(stdout)
        logging.error(stderr)
        return ""
    else:
        kubeconfig_as_dict = yaml.load(json.loads(stdout)['kubeconfig'], Loader=yaml.Loader)
        del kubeconfig_as_dict['clusters'][0]['cluster']['certificate-authority-data']
        kubeconfig_path = my_path + "/kubeconfig_" + type
        with open(kubeconfig_path, "w") as kubeconfig_file:
            yaml.dump(kubeconfig_as_dict, kubeconfig_file)
        logging.debug('Downloaded kubeconfig file for Cluster ID %s and stored at %s' % (cluster_id, kubeconfig_path))
        return kubeconfig_path


def _download_cluster_admin_kubeconfig(rosa_cmnd, cluster_name, my_path):
    cluster_admin_create_time = int(time.time())
    return_data = {}
    logging.info('Creating cluster-admin user on cluster %s (30 minutes timeout)' % cluster_name)
    rosa_create_admin_debug_log = open(my_path + "/" + 'rosa_create_admin_debug.log', 'w')
    rosa_create_admin_cmd = [rosa_cmnd,  "create", "admin", "-c", cluster_name, "-o", "json", "--debug"]
    logging.debug(rosa_create_admin_cmd)
    # Waiting 30 minutes for cluster-admin user to be created
    while datetime.datetime.utcnow().timestamp() < cluster_admin_create_time + 30 * 60:
        if force_terminate:
            logging.error("Exiting cluster access process for %s cluster after capturing Ctrl-C" % cluster_name)
            return return_data
        process = subprocess.Popen(rosa_create_admin_cmd, stdout=subprocess.PIPE, stderr=rosa_create_admin_debug_log, cwd=my_path, universal_newlines=True)
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            logging.warning('Failed to create cluster-admin user on %s with this stdout/stderr:' % cluster_name)
            logging.warning(stdout)
            logging.warning(stderr)
            logging.warning('Waiting 5 seconds for the next try on %s until %s' % (cluster_name, datetime.datetime.fromtimestamp(cluster_admin_create_time + 30 * 60)))
            time.sleep(5)
        else:
            oc_login_time = int(time.time())
            logging.info("cluster-admin user creation succesfull on cluster %s" % cluster_name)
            return_data['cluster-admin-create'] = int(time.time()) - cluster_admin_create_time
            logging.info('Trying to login on cluster %s (30 minutes timeout until %s, 5s timeout on oc command)' % (cluster_name, datetime.datetime.fromtimestamp(oc_login_time + 30 * 60)))
            start_json = stdout.find("{")
            while datetime.datetime.utcnow().timestamp() < oc_login_time + 30 * 60:
                if force_terminate:
                    logging.error("Exiting cluster access process for %s cluster after capturing Ctrl-C" % cluster_name)
                    return return_data
                oc_login_cmnd = ["oc", "login", json.loads(stdout[start_json:])['api_url'], "--username", json.loads(stdout[start_json:])['username'], "--password", json.loads(stdout[start_json:])['password'], "--kubeconfig", my_path + "/kubeconfig", "--insecure-skip-tls-verify=true", "--request-timeout=30s"]
                logging.debug(oc_login_cmnd)
                process_oc_login = subprocess.Popen(oc_login_cmnd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=my_path, universal_newlines=True)
                stdout_oc_login, stderr_oc_login = process_oc_login.communicate()
                if process_oc_login.returncode != 0:
                    logging.warning('Failed to login on cluster %s with cluster-admin user with this stdout/stderr:' % cluster_name)
                    logging.warning(stdout_oc_login)
                    logging.warning(stderr_oc_login)
                    logging.warning('Waiting 5 seconds until %s for the next try on %s' % (cluster_name, datetime.datetime.fromtimestamp(oc_login_time + 30 * 60)))
                    time.sleep(5)
                else:
                    oc_adm_time_start = int(time.time())
                    logging.info("Login succesfull on cluster %s" % cluster_name)
                    return_data['cluster-admin-login'] = int(time.time()) - oc_login_time
                    return_data['kubeconfig'] = my_path + "/kubeconfig"
                    myenv = os.environ.copy()
                    myenv["KUBECONFIG"] = return_data['kubeconfig']
                    oc_adm_cmnd = ["oc", "adm", "top", "images"]
                    logging.info('Trying to perform oc adm command on cluster %s until %s' % (cluster_name, datetime.datetime.fromtimestamp(oc_adm_time_start + 30 * 60)))
                    logging.debug(oc_adm_cmnd)
                    while datetime.datetime.utcnow().timestamp() < oc_adm_time_start + 30 * 60:
                        if force_terminate:
                            logging.error("Exiting cluster access process for %s cluster after capturing Ctrl-C" % cluster_name)
                            return return_data
                        process_oc_adm = subprocess.Popen(oc_adm_cmnd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=my_path,  universal_newlines=True, env=myenv)
                        stdout_oc_adm, stderr_oc_adm = process_oc_adm.communicate()
                        if process_oc_adm.returncode != 0:
                            logging.warning("Failed to perform oc adm command on %s with downloaded kubeconfig %s" % (cluster_name, my_path + "/kubeconfig"))
                            logging.warning(stdout_oc_adm)
                            logging.warning(stderr_oc_adm)
                            logging.warning('Waiting 5 seconds for the next try on %s' % cluster_name)
                            time.sleep(5)
                        else:
                            logging.info("Verified admin access to %s, using %s kubeconfig file." % (cluster_name, my_path + "/kubeconfig"))
                            return_data['cluster-oc-adm'] = int(time.time()) - oc_adm_time_start
                            return return_data
                    logging.error("Failed to execute `oc adm top images` cluster %s after 30 minutes. Exiting" % cluster_name)
                    return return_data
            logging.error("Failed to login on cluster %s after 30 minutes retries. Exiting" % cluster_name)
            return return_data
    logging.error("Failed to create cluster-admin user on cluster %s after 30 minutes. Exiting" % cluster_name)
    return return_data


def _preflight_wait(rosa_cmnd, cluster_id, cluster_name):
    return_data = {}
    start_time = int(time.time())
    previous_status = ""
    logging.info('Collecting preflight times for cluster %s during 60 minutes until %s' % (cluster_name, datetime.datetime.fromtimestamp(start_time + 60 * 60)))
    # Waiting 1 hour for preflight checks to end
    while datetime.datetime.utcnow().timestamp() < start_time + 60 * 60:
        if force_terminate:
            logging.error("Exiting preflight times capturing on %s cluster after capturing Ctrl-C" % cluster_name)
            return 0
        logging.info('Getting status for cluster %s' % cluster_name)
        status_cmnd = [rosa_cmnd, "describe", "cluster", "-c", cluster_id, "-o", "json"]
        logging.debug(status_cmnd)
        status_process = subprocess.Popen(status_cmnd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        status_stdout, status_stderr = status_process.communicate()
        current_time = int(time.time())
        try:
            current_status = json.loads(status_stdout)['state']
        except Exception as err:
            logging.error("Cannot load metadata for cluster %s" % cluster_name)
            logging.error(err)
            continue
        if current_status != previous_status and previous_status != "":
            return_data[previous_status] = current_time - start_time
            start_time = current_time
            logging.info("Cluster %s moved from %s status to %s status after %d seconds" % (cluster_name, previous_status, current_status, return_data[previous_status]))
            if current_status == "installing":
                logging.info("Cluster %s is on installing status. Exiting preflights waiting..." % cluster_name)
                return return_data
        else:
            logging.info("Cluster %s on %s status. Waiting 2 seconds until %s for next check" % (cluster_name, current_status, datetime.datetime.fromtimestamp(start_time + 60 * 60)))
            time.sleep(1)
        previous_status = current_status
    logging.error("Cluster %s on %s status (not installing) after 60 minutes. Exiting preflight waiting..." % (cluster_name, current_status))
    return return_data


def _namespace_wait(kubeconfig, cluster_id, cluster_name, type):
    start_time = int(time.time())
    logging.info('Capturing namespace creation time on %s Cluster for %s. Waiting 30 minutes until %s' % (type, cluster_name, datetime.datetime.fromtimestamp(start_time + 30 * 60)))
    myenv = os.environ.copy()
    myenv["KUBECONFIG"] = kubeconfig
    projects_cmnd = ["oc", "get", "projects", "--output", "json"]
    # Waiting 30 minutes for preflight checks to end
    while datetime.datetime.utcnow().timestamp() < start_time + 30 * 60:
        if force_terminate:
            logging.error("Exiting namespace creation waiting for %s on the %s cluster after capturing Ctrl-C" % (cluster_name, type))
            return 0
        projects_process = subprocess.Popen(projects_cmnd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=myenv)
        logging.debug(projects_cmnd)
        projects_process_stdout, projects_process_stderr = projects_process.communicate()
        if projects_process.returncode != 0:
            logging.warning(projects_process_stdout)
            logging.warning(projects_process_stderr)
            logging.warning("Failed to get the project list on the %s Cluster. Retrying in 5 seconds. Waiting until %s" % (type, datetime.datetime.fromtimestamp(start_time + 30 * 60)))
            time.sleep(5)
        else:
            try:
                projects_json = json.loads(projects_process_stdout)
            except Exception as err:
                logging.warning(projects_process_stdout)
                logging.warning(projects_process_stderr)
                logging.warning(err)
                logging.warning("Failed to get the project list on the %s Cluster. Retrying in 5 seconds until %s" % (type, datetime.datetime.fromtimestamp(start_time + 30 * 60)))
                time.sleep(5)
                continue
            projects = projects_json['items'] if 'items' in projects_json else []
            namespace_count = 0
            for project in projects:
                if 'metadata' in project and 'name' in project['metadata'] and cluster_id in project['metadata']['name']:
                    namespace_count += 1
            if (type == "Service" and namespace_count == 2) or (type == "Management" and namespace_count == 3):
                end_time = int(time.time())
                logging.info("Namespace for %s created in %s cluster in %d seconds" % (cluster_name, type, (end_time - start_time)))
                return end_time - start_time
            else:
                logging.warning("Namespace for %s not found in %s Cluster. Retrying in 5 seconds until %s" % (cluster_name, type, datetime.datetime.fromtimestamp(start_time + 30 * 60)))
                time.sleep(5)
    logging.error("Failed to get namespace for %s on the %s cluster after 15 minutes" % (cluster_name, type))
    return 0


def _build_cluster(ocm_cmnd, rosa_cmnd, cluster_name_seed, must_gather_all, provision_shard, create_vpc, vpc_info, wait_time, cluster_load, load_duration, job_iterations, worker_nodes, my_path, my_uuid, my_inc, es, es_url, index, index_retry, service_cluster_name, sc_kubeconfig, all_clusters_installed, oidc_config_id, workload_type, kube_burner_version, e2e_git_details, git_branch, operator_roles_prefix):
    # pass that dir as the cwd to subproccess
    cluster_path = my_path + "/" + cluster_name_seed + "-" + str(my_inc).zfill(4)
    os.mkdir(cluster_path)
    logging.debug('Attempting cluster installation')
    logging.debug('Output directory set to %s' % cluster_path)
    cluster_name = cluster_name_seed + "-" + str(my_inc).zfill(4)
    git_user = _get_git_repo_details()
    if git_user != "cloud-bulldozer":
        GITHUB_USERNAME = git_user
    else:
        GITHUB_USERNAME = "cloud-bulldozer"

    cluster_cmd = [rosa_cmnd, "create", "cluster", "--cluster-name", cluster_name, f"--tags=User:{GITHUB_USERNAME}", "--replicas", str(worker_nodes), "--hosted-cp", "--sts", "--mode", "auto", "-y", "--output", "json", "--oidc-config-id", oidc_config_id]
    if create_vpc:
        cluster_cmd.append("--subnet-ids")
        cluster_cmd.append(vpc_info[1])
    if provision_shard:
        cluster_cmd.append("--properties")
        cluster_cmd.append("provision_shard_id:" + provision_shard)
    if args.wildcard_options:
        for param in args.wildcard_options.split():
            cluster_cmd.append(param)
    if operator_roles_prefix:
        cluster_cmd.append("--operator-roles-prefix")
        cluster_cmd.append(cluster_name_seed)
    logging.debug(cluster_cmd)
    installation_log = open(cluster_path + "/" + 'installation.log', 'w')
    cluster_start_time = int(time.time())
    logging.info("Trying to install %s cluster with %d workers up to 5 times" % (cluster_name, worker_nodes))
    metadata = {}
    trying = 0
    while trying <= 5:
        if force_terminate:
            logging.error("Exiting cluster creation for %s after capturing Ctrl-C" % cluster_name)
            return 0
        process = subprocess.Popen(cluster_cmd, stdout=installation_log, stderr=installation_log, preexec_fn=disable_signals)
        stdout, stderr = process.communicate()
        trying += 1
        if process.returncode != 0:
            metadata['install_try'] = trying
            logging.debug(stdout)
            logging.debug(stderr)
            if trying <= 5:
                logging.warning("Try: %d/5. Cluster %s installation failed, retrying in 15 seconds" % (trying, cluster_name))
                time.sleep(15)
            else:
                cluster_end_time = int(time.time())
                metadata['status'] = "Not Installed"
                cluster_load = False
                logging.error("%s Cluster installation failed after 5 retries" % cluster_name)
                logging.debug(stdout)
        else:
            logging.info("Cluster %s installation started on the %d try" % (cluster_name, trying))
            metadata = get_metadata(cluster_name, rosa_cmnd)
            metadata['install_try'] = trying
            with concurrent.futures.ThreadPoolExecutor() as executor:
                preflight_ch = executor.submit(_preflight_wait, rosa_cmnd, metadata['cluster_id'], cluster_name)
                sc_namespace = executor.submit(_namespace_wait, sc_kubeconfig, metadata['cluster_id'], cluster_name, "Service") if sc_kubeconfig != "" else 0
                preflight_checks = preflight_ch.result()
                sc_namespace_timing = sc_namespace.result() if sc_kubeconfig != "" else 0
            mgmt_cluster_name = _get_mgmt_cluster(sc_kubeconfig, metadata['cluster_id'], cluster_name) if sc_kubeconfig != "" else None
            mgmt_metadata = _get_mgmt_cluster_info(ocm_cmnd, mgmt_cluster_name, es, index, index_retry, uuid) if mgmt_cluster_name else None
            mgmt_kubeconfig_path = _download_kubeconfig(ocm_cmnd, mgmt_metadata['cluster_id'], cluster_path, "mgmt") if mgmt_cluster_name else None
            mc_namespace_timing = _namespace_wait(mgmt_kubeconfig_path, metadata['cluster_id'], cluster_name, "Management") if mgmt_kubeconfig_path else 0
            watch_cmd = [rosa_cmnd, "logs", "install", "-c", cluster_name, "--watch"]
            logging.debug(watch_cmd)
            watch_process = subprocess.Popen(watch_cmd, stdout=installation_log, stderr=installation_log, preexec_fn=disable_signals)
            watch_stdout, watch_stderr = watch_process.communicate()
            cluster_end_time = int(time.time())
            metadata = get_metadata(cluster_name, rosa_cmnd)
            return_data = _download_cluster_admin_kubeconfig(rosa_cmnd, cluster_name, cluster_path)
            kubeconfig = return_data['kubeconfig'] if 'kubeconfig' in return_data else ""
            metadata['cluster-admin-create'] = return_data['cluster-admin-create'] if 'cluster-admin-create' in return_data else 0
            metadata['cluster-admin-login'] = return_data['cluster-admin-login'] if 'cluster-admin-login' in return_data else 0
            metadata['cluster-oc-adm'] = return_data['cluster-oc-adm'] if 'cluster-oc-adm' in return_data else 0
            metadata['mgmt_namespace'] = mc_namespace_timing
            metadata['sc_namespace'] = sc_namespace_timing
            metadata['preflight_checks'] = preflight_checks
            if kubeconfig == "":
                logging.error("Failed to download kubeconfig file. Disabling wait for workers and e2e-benchmarking execution on %s" % cluster_name)
                wait_time = 0
                cluster_load = False
                metadata['status'] = "Ready. Not Access"
            if args.machinepool_name:
                _add_machinepools(rosa_cmnd, kubeconfig, metadata,
                                  args.machinepool_name,
                                  args.machinepool_flavour,
                                  args.machinepool_labels,
                                  args.machinepool_taints,
                                  args.machinepool_replicas)
            metadata['workers_ready'] = ""
            metadata['extra_pool_workers_ready'] = ""
            if wait_time != 0:
                with concurrent.futures.ThreadPoolExecutor() as wait_executor:
                    futures = [wait_executor.submit(_wait_for_workers, kubeconfig, worker_nodes, wait_time, cluster_name, "workers"), ]
                    futures.append(wait_executor.submit(_wait_for_workers, kubeconfig, args.machinepool_replicas, wait_time, cluster_name, args.machinepool_name)) if args.machinepool_name else None
                    for future in concurrent.futures.as_completed(futures):
                        logging.debug(future)
                        result = future.result()
                        if result[0] == "workers":
                            default_pool_workers = int(result[1])
                            metadata['workers_ready'] = result[2] if default_pool_workers == worker_nodes else ""
                        else:
                            extra_pool_workers = int(result[1])
                            metadata['extra_pool_workers_ready'] = result[2] if args.machinepool_name and extra_pool_workers == args.machinepool_replicas else ""
                if cluster_load:
                    if default_pool_workers != worker_nodes:
                        logging.error("Insufficient number of workers on default machinepool (%d). Expected: %d. Disabling e2e-benchmarking execution on %s" % (default_pool_workers, worker_nodes, cluster_name))
                        cluster_load = False
                        metadata['status'] = "Ready. Not enough workers on default pool"
                    elif args.machinepool_name and extra_pool_workers != args.machinepool_replicas:
                        logging.error("Insufficient number of workers on extra machinepool %s (%d). Expected: %d. Disabling e2e-benchmarking execution on %s" % (args.machinepool_name, extra_pool_workers, args.machinepool_replicas, cluster_name))
                        cluster_load = False
                        metadata['status'] = "Ready. Not enough workers on extra pool"
                else:
                    logging.info("All workers ready, executing e2e-benchmarking on %s" % cluster_name)
            break
    metadata['mgmt_cluster_name'] = mgmt_cluster_name
    metadata['duration'] = cluster_end_time - cluster_start_time
    metadata['job_iterations'] = str(job_iterations) if cluster_load else 0
    metadata['load_duration'] = load_duration if cluster_load else ""
    metadata['workers'] = str(worker_nodes)
    metadata['uuid'] = my_uuid
    metadata['operation'] = "install"
    metadata['install_method'] = "rosa"
    metadata['cluster_name'] = cluster_name
    try:
        with open(cluster_path + "/metadata_install.json", "w") as metadata_file:
            json.dump(metadata, metadata_file)
    except Exception as err:
        logging.error(err)
        logging.error('Failed to write metadata_install.json file located %s' % cluster_path)
    if es is not None:
        metadata["timestamp"] = datetime.datetime.utcnow().isoformat()
        es_ignored_metadata = ""
        common._index_result(es, index, metadata, es_ignored_metadata, index_retry)
    if cluster_load:
        with all_clusters_installed:
            logging.info('Waiting for all clusters to be installed to start e2e-benchmarking execution on %s' % cluster_name)
            all_clusters_installed.wait()
        logging.info('Executing e2e-benchmarking to add load on the cluster %s with %s nodes during %s with %d iterations' % (cluster_name, str(worker_nodes), load_duration, job_iterations))
        _cluster_load(kubeconfig, cluster_path, cluster_name, mgmt_cluster_name, service_cluster_name, load_duration, job_iterations, es_url, mgmt_kubeconfig_path, workload_type, kube_burner_version, e2e_git_details, git_branch)
        logging.info('Finished execution of e2e-benchmarking workload on %s' % cluster_name)
    if must_gather_all or process.returncode != 0:
        random_sleep = random.randint(60, 300)
        logging.info("Waiting %d seconds before dumping hosted cluster must-gather" % random_sleep)
        time.sleep(random_sleep)
        logging.info("Saving must-gather file of hosted cluster %s" % cluster_name)
        _get_must_gather(cluster_path, cluster_name)
        _get_mgmt_cluster_must_gather(mgmt_kubeconfig_path, my_path)


def _get_workers_ready(kubeconfig, cluster_name):
    myenv = os.environ.copy()
    myenv["KUBECONFIG"] = kubeconfig
    logging.info('Getting node information for cluster %s' % cluster_name)
    nodes_command = ["oc", "get", "nodes", "-o", "json"]
    logging.debug(nodes_command)
    nodes_process = subprocess.Popen(nodes_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=myenv)
    nodes_stdout, nodes_stderr = nodes_process.communicate()
    try:
        nodes_json = json.loads(nodes_stdout)
    except Exception as err:
        logging.error("Cannot load command result for cluster %s" % cluster_name)
        logging.error(err)
        return 0
    nodes = nodes_json['items'] if 'items' in nodes_json else []
    status = []
    for node in nodes:
        nodepool = node['metadata']['labels']['hypershift.openshift.io/nodePool'] if node.get('metadata', {}).get('labels', {}).get('hypershift.openshift.io/nodePool') else ""
        if 'workers' in nodepool:
            conditions = node['status']['conditions'] if node.get('status', {}).get('conditions', {}) else []
            for condition in conditions:
                if 'type' in condition and condition['type'] == 'Ready':
                    status.append(condition['status'])
    status_list = {i: status.count(i) for i in status}
    ready_nodes = status_list['True'] if 'True' in status_list else 0
    return ready_nodes


def _wait_for_workers(kubeconfig, worker_nodes, wait_time, cluster_name, machinepool_name):
    logging.info("Waiting %d minutes for %d workers to be ready on %s machinepool on %s" % (wait_time, worker_nodes, machinepool_name, cluster_name))
    myenv = os.environ.copy()
    myenv["KUBECONFIG"] = kubeconfig
    result = [machinepool_name]
    starting_time = datetime.datetime.utcnow().timestamp()
    logging.debug("Waiting %d minutes for nodes to be Ready on cluster %s until %s" % (wait_time, cluster_name, datetime.datetime.fromtimestamp(starting_time + wait_time * 60)))
    while datetime.datetime.utcnow().timestamp() < starting_time + wait_time * 60:
        if force_terminate:
            logging.error("Exiting workers waiting on the cluster %s after capturing Ctrl-C" % cluster_name)
            return []
        logging.info('Getting node information for cluster %s' % cluster_name)
        nodes_command = ["oc", "get", "nodes", "-o", "json"]
        logging.debug(nodes_command)
        nodes_process = subprocess.Popen(nodes_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=myenv)
        nodes_stdout, nodes_stderr = nodes_process.communicate()
        try:
            nodes_json = json.loads(nodes_stdout)
        except Exception as err:
            logging.error("Cannot load command result for cluster %s. Waiting 15 seconds for next check..." % cluster_name)
            logging.error(err)
            time.sleep(15)
            continue
        nodes = nodes_json['items'] if 'items' in nodes_json else []

        # First we find nodes which label nodePool match the machinepool name and then we check if type:Ready is on the conditions
        ready_nodes = sum(
            len(list(filter(lambda x: x.get('type') == 'Ready' and x.get('status') == "True", node['status']['conditions'])))
            for node in nodes
            if node.get('metadata', {}).get('labels', {}).get('hypershift.openshift.io/nodePool')
            and machinepool_name in node['metadata']['labels']['hypershift.openshift.io/nodePool']
        ) if nodes else 0

        if ready_nodes == worker_nodes:
            logging.info("Found %d/%d ready nodes on machinepool %s for cluster %s. Stopping wait." % (ready_nodes, worker_nodes, machinepool_name, cluster_name))
            result.append(ready_nodes)
            result.append(str(datetime.datetime.utcnow().timestamp() - starting_time))
            return result
        else:
            logging.info("Found %d/%d ready nodes on machinepool %s for cluster %s. Waiting 15 seconds for next check..." % (ready_nodes, worker_nodes, machinepool_name, cluster_name))
            time.sleep(15)
    logging.error("Waiting time expired. After %d minutes there are %d/%d ready nodes on %s machinepool for cluster %s" % (wait_time, ready_nodes, worker_nodes, machinepool_name, cluster_name))
    result.append(ready_nodes)
    result.append("")
    return result


def _add_machinepools(rosa_cmnd, kubeconfig, metadata, machinepool_name, machinepool_flavour, machinepool_labels, machinepool_taints, machinepool_replicas):
    logging.info('Creating %d machinepools %s-ID on %s, one per AWS Zone' % (len(metadata['zones']), machinepool_name, metadata['cluster_name']))
    machines_per_zone = machinepool_replicas // len(metadata['zones'])
    extra_machines = machinepool_replicas % len(metadata['zones'])
    zone_machines = [machines_per_zone] * len(metadata['zones'])
    if extra_machines > 0:
        zone_machines[-1] += extra_machines
    for id, zone in enumerate(metadata['zones']):
        machinepool_cmd = [rosa_cmnd, "create", "machinepool",
                           "--cluster", metadata['cluster_id'],
                           "--instance-type", machinepool_flavour,
                           "--name", machinepool_name + "-" + str(id),
                           "--replicas", str(zone_machines[id]),
                           "--availability-zone", zone,
                           "-y"]
        if machinepool_labels:
            machinepool_cmd.append("--labels")
            machinepool_cmd.append(machinepool_labels)
        if machinepool_taints:
            machinepool_cmd.append("--tains")
            machinepool_cmd.append(machinepool_taints)

        logging.debug(machinepool_cmd)
        machinepool_process = subprocess.Popen(machinepool_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        machinepool_stdout, machinepool_stderr = machinepool_process.communicate()
        if machinepool_process.returncode != 0:
            logging.error('Unable to create machinepool %s on %s' % (machinepool_name + "-" + id, metadata['cluster_name']))
            logging.error(machinepool_stdout.strip().decode("utf-8"))
            logging.error(machinepool_stderr.strip().decode("utf-8"))


def _cluster_load(kubeconfig, my_path, hosted_cluster_name, mgmt_cluster_name, svc_cluster_name, load_duration, jobs, es_url, mgmt_kubeconfig, workload_type, kube_burner_version, e2e_git_details, git_branch):
    load_env = os.environ.copy()
    load_env["KUBECONFIG"] = kubeconfig
    load_env["MC_KUBECONFIG"] = mgmt_kubeconfig if mgmt_kubeconfig else ""
    logging.info('Cloning e2e-benchmarking repo %s', )
    Repo.clone_from(e2e_git_details, my_path + '/e2e-benchmarking', branch=git_branch)
    url = "https://github.com/cloud-bulldozer/kube-burner/releases/download/v" + kube_burner_version + "/kube-burner-" + kube_burner_version + "-Linux-x86_64.tar.gz"
    dest = my_path + "/kube-burner-" + kube_burner_version + "-Linux-x86_64.tar.gz"
    response = requests.get(url, stream=True)
    with open(dest, 'wb') as f:
        f.write(response.raw.read())
    untar_kb = ["tar", "xzf", my_path + "/kube-burner-" + kube_burner_version + "-Linux-x86_64.tar.gz", "-C", my_path + "/"]
    logging.debug(untar_kb)
    untar_kb_process = subprocess.Popen(untar_kb, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=load_env)
    stdout, stderr = untar_kb_process.communicate()
    if untar_kb_process.returncode != 0:
        logging.error("Failed to untar Kube-burner from %s to %s" % (my_path + "/kube-burner-" + kube_burner_version + "-Linux-x86_64.tar.gz", my_path + "/kube-burner"))
        return 1
    os.chmod(my_path + '/kube-burner', 0o777)
    os.chdir(my_path + '/e2e-benchmarking/workloads/kube-burner-ocp-wrapper')
    load_env["ITERATIONS"] = str(jobs)
    load_env["EXTRA_FLAGS"] = "--churn-duration=" + load_duration + " --churn-percent=10 --churn-delay=30s --timeout=24h"
    if es_url is not None:
        load_env["ES_SERVER"] = es_url
    load_env["LOG_LEVEL"] = "debug"
    load_env["WORKLOAD"] = str(workload_type)
    load_env["KUBE_DIR"] = my_path
    load_command = ["./run.sh"]
    logging.debug(load_command)
    load_log = open(my_path + '/cluster_load.log', 'w')
    if not force_terminate:
        load_process = subprocess.Popen(load_command, stdout=load_log, stderr=load_log, env=load_env)
        load_process_stdout, load_process_stderr = load_process.communicate()
    else:
        logging.warning("Not starting e2e on cluster %s after capturing Ctrl-C" % hosted_cluster_name)


def _get_must_gather(cluster_path, cluster_name):
    myenv = os.environ.copy()
    myenv["KUBECONFIG"] = cluster_path + "/kubeconfig"
    logging.info('Gathering facts of hosted cluster %s' % cluster_name)
    must_gather_command = ["oc", "adm", "must-gather", "--dest-dir", cluster_path + "/must_gather"]
    logging.debug(must_gather_command)
    must_gather_log = open(cluster_path + '/must_gather.log', 'w')
    must_gather_process = subprocess.Popen(must_gather_command, stdout=must_gather_log, stderr=must_gather_log, env=myenv)
    must_gather_stdout, must_gather_stderr = must_gather_process.communicate()
    if must_gather_process.returncode != 0:
        logging.error("Failed to obtain must-gather from %s" % cluster_name)
        return 1
    logging.info('Compressing must gather artifacts on %s file' % cluster_path + "/must_gather.tar.gz")
    must_gather_compress_command = ["tar", "czvf", "must_gather.tar.gz", cluster_path + "/must_gather"]
    logging.debug(must_gather_compress_command)
    must_gather_compress_process = subprocess.Popen(must_gather_compress_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=myenv)
    must_gather_compress_stdout, must_gather_compress_stderr = must_gather_compress_process.communicate()
    if must_gather_compress_process.returncode != 0:
        logging.error("Failed to compress must-gather of %s cluster from %s to %s" % (cluster_name, cluster_path + "/must_gather", cluster_path + "/must_gather.tar.gz"))
        return 1
    logging.info('Deleting non-compressed must-gather files of hosted cluster %s' % cluster_name)
    must_gather_delete_command = ["rm", "-rf", cluster_path + "/must_gather"]
    logging.debug(must_gather_delete_command)
    must_gather_delete_process = subprocess.Popen(must_gather_delete_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=myenv)
    must_gather_delete_stdout, must_gather_delete_stderr = must_gather_delete_process.communicate()
    if must_gather_delete_process.returncode != 0:
        logging.error("Failed to delete non-compressed must-gather files of hosted cluster %s" % cluster_name)
        return 1


def _get_mgmt_cluster_must_gather(kubeconfig, my_path):
    myenv = os.environ.copy()
    myenv["KUBECONFIG"] = kubeconfig
    logging.info('Gathering facts of management cluster')
    must_gather_command = ["oc", "adm", "must-gather", "--dest-dir", my_path + "/must_gather"]
    logging.debug(must_gather_command)
    must_gather_log = open(my_path + '/management_cluster_must_gather.log', 'w')
    must_gather_process = subprocess.Popen(must_gather_command, stdout=must_gather_log, stderr=must_gather_log, env=myenv)
    must_gather_stdout, must_gather_stderr = must_gather_process.communicate()
    if must_gather_process.returncode != 0:
        logging.error("Failed to obtain must-gather from Management Cluster")
        return 1
    logging.info('Compressing must gather artifacts on %s file' % (my_path + "/management_cluster_must_gather.tar.gz"))
    must_gather_compress_command = ["tar", "czvf", my_path + "/management_cluster_must_gather.tar.gz", my_path + "/must_gather"]
    logging.debug(must_gather_compress_command)
    must_gather_compress_process = subprocess.Popen(must_gather_compress_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=myenv)
    must_gather_compress_stdout, must_gather_compress_stderr = must_gather_compress_process.communicate()
    if must_gather_compress_process.returncode != 0:
        logging.error("Failed to compress must-gather of Management Cluster from %s to %s" % (my_path + "/must_gather", my_path + "must_gather.tar.gz"))
        return 1
    logging.info('Deleting non-compressed must-gather files of Management Cluster')
    must_gather_delete_command = ["rm", "-rf", my_path + "/must_gather"]
    logging.debug(must_gather_delete_command)
    must_gather_delete_process = subprocess.Popen(must_gather_delete_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=myenv)
    must_gather_delete_stdout, must_gather_delete_stderr = must_gather_delete_process.communicate()
    if must_gather_delete_process.returncode != 0:
        logging.error("Failed to delete non-compressed must-gather files of Management Cluster")
        return 1


def get_metadata(cluster_name, rosa_cmnd):
    metadata = {}
    logging.info('Getting information for cluster %s' % cluster_name)
    metadata_hosted = [rosa_cmnd, "describe", "cluster", "-c", cluster_name, "-o", "json"]
    logging.debug(metadata_hosted)
    metadata_hosted_process = subprocess.Popen(metadata_hosted, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    metadata_hosted_stdout, metadata_hosted_stderr = metadata_hosted_process.communicate()
    try:
        metadata_hosted_info = json.loads(metadata_hosted_stdout)
        metadata["cluster_name"] = metadata_hosted_info['name']
        metadata["cluster_id"] = metadata_hosted_info['id']
        metadata["network_type"] = metadata_hosted_info['network']['type']
        metadata['workers'] = metadata_hosted_info['nodes']['compute']
        metadata["status"] = metadata_hosted_info['state']
        metadata["version"] = metadata_hosted_info['version']['raw_id']
        metadata["zones"] = metadata_hosted_info['nodes']['availability_zones']
    except Exception as err:
        logging.error("Cannot load metadata for cluster %s" % cluster_name)
        logging.error(err)
    return metadata


def _watcher(rosa_cmnd, my_path, cluster_name_seed, cluster_count, delay, my_uuid, all_clusters_installed, cluster_load):
    time.sleep(60)
    logging.info('Watcher thread started')
    logging.info('Getting status every %d seconds' % int(delay))
    # watcher will stop iterating and notify to run e2e if one of the below conditions met
    # 1) look if all the clusters move to ready state or
    # 2) if the user created e2e file in the test directory
    file_path = os.path.join(my_path, "e2e")
    if os.path.exists(file_path):
        os.remove(file_path)
        time.sleep(60)

    cmd = [rosa_cmnd, "list", "clusters", "-o", "json"]
    while not force_terminate:
        logging.debug(cmd)
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        stdout, stderr = process.communicate()
        current_cluster_count = 0
        installed_clusters = 0
        clusters_with_all_workers = 0
        state = {}
        error = []
        try:
            clusters = json.loads(stdout)
        except ValueError as err:
            logging.error("Failed to get hosted clusters list: %s" % err)
            logging.error(stdout)
            logging.error(stderr)
            clusters = {}
        for cluster in clusters:
            if 'name' in cluster and cluster_name_seed in cluster['name']:
                current_cluster_count += 1
                state_key = cluster['state'] if 'state' in cluster else ""
                if state_key == "error":
                    error.append(cluster['name'])
                elif state_key == "ready":
                    state[state_key] = state.get(state_key, 0) + 1
                    installed_clusters += 1
                    required_workers = cluster['nodes']['compute']
                    ready_workers = _get_workers_ready(my_path + "/" + cluster['name'] + "/kubeconfig", cluster['name'])
                    if ready_workers == required_workers:
                        clusters_with_all_workers += 1
                elif state_key != "":
                    state[state_key] = state.get(state_key, 0) + 1
        logging.info('Requested Clusters for test %s: %d of %d' % (my_uuid, current_cluster_count, cluster_count))
        state_output = ""
        for i in state.items():
            state_output += "(" + str(i[0]) + ": " + str(i[1]) + ") "
            logging.info(state_output)
        if error:
            logging.warning('Clusters in error state: %s' % error)
        if os.path.isfile(file_path):
            with all_clusters_installed:
                logging.info("User requested the wrapper to start e2e testing by creating e2e file in the test directory")
                all_clusters_installed.notify_all()
            break
        elif installed_clusters == cluster_count:
            if cluster_load and clusters_with_all_workers == cluster_count:
                logging.info('All clusters on ready status and all clusters with all workers ready. Waiting 5 extra minutes to allow all cluster installations to arrive notify status')
                time.sleep(300)
                with all_clusters_installed:
                    logging.info('All requested clusters on ready status, notifying threads to start e2e-benchmarking processes')
                    all_clusters_installed.notify_all()
                    break
            elif not cluster_load:
                logging.info('All clusters on ready status and no loading process required. Waiting 5 extra minutes to allow all cluster installations to finish.')
                time.sleep(300)
                break
            else:
                logging.info("Waiting %d seconds for next watcher run" % delay)
                time.sleep(delay)
        else:
            logging.info("Waiting %d seconds for next watcher run" % delay)
            time.sleep(delay)
    with all_clusters_installed:
        all_clusters_installed.notify_all()
    logging.info('Watcher terminated')

def _get_git_repo_details():
    # Get the Git remote URL for the current working directory
    command = ['git', 'config', '--get', 'remote.origin.url']
    result = subprocess.run(command, capture_output=True, text=True)
    git_repo_url = result.stdout.strip()

    if git_repo_url.startswith('https://github.com/'):
        git_path = git_repo_url[len('https://github.com/'):]
        git_user = git_path.split('/')[0]
        return git_user

def _cleanup_cluster(rosa_cmnd, cluster_name, my_path, my_uuid, es, index, index_retry):
    cluster_path = my_path + "/" + cluster_name
    metadata = get_metadata(cluster_name, rosa_cmnd)
    logging.debug('Destroying cluster name: %s' % cluster_name)
    del_cmd = [rosa_cmnd, "delete", "cluster", "-c", cluster_name, "-y", "--watch"]
    logging.debug(del_cmd)
    cleanup_log = open(cluster_path + '/cleanup.log', 'w')
    cluster_start_time = int(time.time())
    process = subprocess.Popen(del_cmd, stdout=cleanup_log, stderr=cleanup_log, preexec_fn=disable_signals)
    stdout, stderr = process.communicate()
    cluster_delete_end_time = int(time.time())

    if process.returncode != 0:
        logging.error('Hosted cluster destroy failed for cluster name %s with this stdout/stderr:' % cluster_name)
        logging.error(stdout)
        logging.error(stderr)
        metadata['status'] = "not deleted"
    else:
        logging.debug('Confirm cluster %s deleted by attempting to describe the cluster. This should fail if the cluster is removed.' % cluster_name)
        check_cmd = [rosa_cmnd, "describe", "cluster", "-c", cluster_name]
        process_check = subprocess.Popen(check_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process_check.communicate()
        if process_check.returncode != 0:
            logging.debug('Destroying STS associated resources of cluster name: %s' % cluster_name)
            delete_operator_roles = [rosa_cmnd, "delete", "operator-roles", "--prefix", cluster_name, "-m", "auto", "-y"]
            process_operator = subprocess.Popen(delete_operator_roles, stdout=cleanup_log, stderr=cleanup_log, preexec_fn=disable_signals)
            stdout, stderr = process_operator.communicate()
            if process_operator.returncode != 0:
                logging.error("Failed to delete operator roles on cluster %s" % cluster_name)
        else:
            logging.error('Cluster %s still in list of clusters. Not Removing Roles' % cluster_name)
            metadata['status'] = "not deleted"

    cluster_end_time = int(time.time())
    metadata['install_method'] = "rosa"
    metadata['duration'] = cluster_delete_end_time - cluster_start_time
    metadata['all_duration'] = cluster_end_time - cluster_start_time
    metadata['job_iterations'] = ""
    metadata['load_duration'] = ""
    metadata['operation'] = "destroy"
    metadata['uuid'] = my_uuid
    try:
        with open(my_path + "/" + cluster_name + "/metadata_destroy.json", "w") as metadata_file:
            json.dump(metadata, metadata_file)
    except Exception as err:
        logging.error(err)
        logging.error('Failed to write metadata_destroy.json file located %s' % cluster_path)
    if es is not None:
        metadata["timestamp"] = datetime.datetime.utcnow().isoformat()
        es_ignored_metadata = ""
        common._index_result(es, index, metadata, es_ignored_metadata, index_retry)


def main():
    parser = argparse.ArgumentParser(description="hypershift wrapper script",
                                     parents=[parentParsers.esParser,
                                              parentParsers.runnerParser,
                                              parentParsers.clusterParser,
                                              parentParsers.machinepoolParser,
                                              parentParsers.logParser])
    parser.add_argument(
        '--aws-account-file',
        type=str,
        required=True,
        help='AWS account file to use')
    parser.add_argument(
        '--aws-profile',
        type=str,
        help='AWS profile to use if more than one are present on aws config file',
        required=True)
    parser.add_argument(
        '--aws-region',
        type=str,
        help='AWS region to be used',
        default="us-east-2")
    parser.add_argument(
        '--ocm-token',
        type=str,
        required=True,
        help='Token to be used by OCM and ROSA commands')
    parser.add_argument(
        '--provision-shard',
        required=False,
        type=str,
        help='Provision Shard used to deploy the Hosted Clusters')
    parser.add_argument(
        '--workers',
        type=str,
        required=True,
        default='3',
        help='Number of workers for the hosted cluster (min: 3). If list (comma separated), iteration over the list until reach number of clusters')
    parser.add_argument(
        '--ocm-url',
        type=str,
        help='OCM environment',
        default='https://api.stage.openshift.com')
    parser.add_argument(
        '--ocm-cli',
        type=str,
        help='Full path to the ocm cli binary. If not provided we will download it from GitHub')
    parser.add_argument(
        '--ocm-cli-version',
        type=str,
        help='When downloading from GitHub, release to download. (Default: latest, to download the most recent release)',
        default='latest')
    parser.add_argument(
        '--rosa-env',
        type=str,
        help='ROSA environment (staging, integration). Do not set any for production')
    parser.add_argument(
        '--rosa-cli',
        type=str,
        help='Full path to the rosa cli binary. If not provided we will download it from github')
    parser.add_argument(
        '--rosa-cli-version',
        type=str,
        help='When downloading from GitHub, release to download. (Default: latest, to download the most recent release)',
        default='latest')
    parser.add_argument(
        '--rosa-init',
        dest='rosa_init',
        action='store_true',
        help='Execute `rosa init` command to configure AWS account')
    parser.add_argument(
        '--add-cluster-load',
        action='store_true',
        help='Execute e2e script after hosted cluster is installed to load it')
    parser.add_argument(
        '--cluster-load-duration',
        type=str,
        default='4h',
        help='CHURN_DURATION parameter used on the e2e script')
    parser.add_argument(
        '--cluster-load-jobs-per-worker',
        type=int,
        default=10,
        help='Optimus number of job iterations per worker. Workload will scale it to the number of workers')
    parser.add_argument(
        '--cluster-load-job-variation',
        type=int,
        default=0,
        help='Percentage of variation of jobs to execute. Job iterations will be a number from jobs_per_worker * workers * (-X%% to +X%%)')
    parser.add_argument(
        '--workers-wait-time',
        type=int,
        default=60,
        help="Waiting time in minutes for the workers to be Ready after cluster installation or machinepool creation . If 0, do not wait. Default: 60 minutes")
    parser.add_argument(
        '--terraform-cli',
        type=str,
        help='Full path to the terraform cli binary.')
    parser.add_argument(
        '--terraform-retry',
        type=int,
        default=5,
        help="Number of retries when executing terraform commands")
    parser.add_argument(
        '--create-vpc',
        action='store_true',
        help='If selected, one VPC will be create for each Hosted Cluster')
    parser.add_argument(
        '--clusters-per-vpc',
        type=int,
        default=1,
        choices=range(1, 11),
        help='Number of clusters to create on each VPC. Default: 1, Max: 10')
    parser.add_argument(
        '--must-gather-all',
        action='store_true',
        help='If selected, collect must-gather from all cluster, if not, only collect from failed clusters')
    parser.add_argument(
        '--oidc-config-id',
        type=str,
        help='Pass a custom oidc config id to use for the oidc provider. NOTE: this is not deleted on cleanup')
    parser.add_argument(
        '--common-operator-roles',
        action='store_true',
        help='Create unique operator roles and use them on all the cluster installations')
    parser.add_argument(
        '--workload-type',
        type=str,
        help="Pass the workload type: cluster-density, cluster-density-v2, cluster-density-ms",
        default="cluster-density-ms")
    parser.add_argument(
        '--kube-burner-version',
        type=str,
        help='Kube-burner version, if none provided defaults to 1.5 ',
        default='1.5')
    parser.add_argument(
        '--e2e-git-details',
        type=str,
        help='Supply the e2e-benchmarking Git URL',
        default="https://github.com/cloud-bulldozer/e2e-benchmarking.git")
    parser.add_argument(
        '--git-branch',
        type=str,
        help='Specify a desired branch of the corresponding git',
        default='master')

# Delete following parameter and code when default security group wont be used
    parser.add_argument(
        '--manually-cleanup-secgroups',
        action='store_true',
        help='If selected, delete security groups before deleting the VPC')
    parser.add_argument(
        '--wait-before-cleanup',
        type=int,
        default=0,
        help="Number of minutes to wait before cleanup clusters")

    global args
    args = parser.parse_args()

    logger = logging.getLogger()
    logger.setLevel(args.log_level.upper())
    log_format = logging.Formatter(
        '%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    consolelog = logging.StreamHandler()
    consolelog.setFormatter(log_format)
    logger.addHandler(consolelog)
    if args.log_file is not None:
        logging.info('Logging to file: %s' % args.log_file)
        common._create_path(os.path.dirname(args.log_file))
        logfile = logging.FileHandler(args.log_file)
        logfile.setFormatter(log_format)
        logger.addHandler(logfile)
        logging.info('Logging to file: %s' % args.log_file)
    else:
        logging.info('Logging to console')

    if args.add_cluster_load and args.workers_wait_time == 0:
        parser.error("Workers wait time > 0 expected when --add-cluster-load is used")

    # global uuid to assign for the group of clusters created. each cluster will have its own cluster-id
    my_uuid = args.uuid
    if my_uuid is None:
        my_uuid = str(uuid.uuid4())
    logging.info('Test running with UUID: %s' % my_uuid)

    my_path = args.path
    if my_path is None:
        my_path = '/tmp/' + my_uuid
    logging.info('Using %s as working directory' % (my_path))
    common._create_path(my_path)

    try:
        logging.debug('Saving test UUID to the working directory')
        uuid_file = open(my_path + '/uuid', 'x')
        uuid_file.write(my_uuid)
        uuid_file.close()
    except Exception as err:
        logging.debug('Cannot write file %s/uuid' % my_path)
        logging.error(err)
        exit(1)

    es = common._connect_to_es(args.es_url, args.es_insecure) if args.es_url is not None else None

    logging.debug('Validating --workers %s parameter' % args.workers)
    pattern = re.compile(r"^(\d+)(,\s*\d+)*$")
    if args.workers.isdigit() and int(args.workers) % 3 != 0:
        logging.error("Invalid value for parameter  \"--workers %s\". If digit, it must be divisible by 3" % args.workers)
        exit(1)
    elif bool(pattern.match(args.workers)):
        for num in args.workers.split(","):
            if int(num) < 3 or int(num) % 3 != 0:
                logging.error("Invalid value for parameter \"--workers %s\". Value %s must be divisible by 3" % (args.workers, num))
                exit(1)
    logging.info("Workers parameter \"--workers %s\" validated" % args.workers)

    if os.path.exists(args.aws_account_file):
        logging.info('AWS account file found. Loading account information')
        aws_config = configparser.RawConfigParser()
        aws_config.read(args.aws_account_file)
        if len(aws_config.sections()) == 1:
            profile = aws_config.sections()[0]
        else:
            if not args.aws_profile:
                parser.error("Multiple profiles detected on AWS credentials file but no --aws-profile parameter")
            else:
                if args.aws_profile not in aws_config.sections():
                    parser.error("profile %s especified as --aws-profile not found on AWS credentials file %s" % (args.aws_profile, args.aws_account_file))
                else:
                    profile = args.aws_profile
        if 'aws_access_key_id' not in aws_config[profile] or 'aws_secret_access_key' not in aws_config[profile]:
            parser.error("Missing keys for profile on AWS credentials file")
        else:
            logging.info('AWS configuration verified for profile %s on file %s' % (profile, args.aws_account_file))
            os.environ['AWS_PROFILE'] = profile
            os.environ['AWS_REGION'] = args.aws_region
            os.environ["AWS_ACCESS_KEY_ID"] = aws_config[profile]['aws_access_key_id']
            os.environ["AWS_SECRET_ACCESS_KEY"] = aws_config[profile]['aws_secret_access_key']
    else:
        logging.error('AWS Account configuration file not found at %s' % args.aws_account_file)
        exit(1)

    cluster_name_seed = common._generate_cluster_name_seed(args.cluster_name_seed)

    try:
        logging.debug('Saving cluster name seed %s to the working directory' % cluster_name_seed)
        seed_file = open(my_path + '/cluster_name_seed', 'x')
        seed_file.write(cluster_name_seed)
        seed_file.close()
    except Exception as err:
        logging.debug('Cannot write file %s/cluster_name_seed' % my_path)
        logging.error(err)
        exit(1)

    terraform_cmnd = ""
    if args.create_vpc:
        if args.terraform_cli is None:
            parser.error("--terraform-cli is required when using --create-vpc")
        else:
            os.mkdir(my_path + "/terraform")
            shutil.copyfile(sys.path[0] + "/terraform/setup-vpcs.tf", my_path + "/terraform/setup-vpcs.tf")
            terraform_cmnd = _verify_terraform(args.terraform_cli, my_path + "/terraform")

    ocm_cmnd, rosa_cmnd = _verify_cmnds(args.ocm_cli, args.rosa_cli, my_path, args.ocm_cli_version, args.rosa_cli_version)

    logging.info('Attempting to log in OCM using `ocm login`')
    ocm_login_command = [ocm_cmnd, "login", "--url=" + args.ocm_url, "--token=" + args.ocm_token]
    logging.debug(ocm_login_command)
    ocm_login_process = subprocess.Popen(ocm_login_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ocm_login_stdout, ocm_login_stderr = ocm_login_process.communicate()
    if ocm_login_process.returncode != 0:
        logging.error('%s unable to execute `ocm login`' % ocm_cmnd)
        logging.error(ocm_login_stderr.strip().decode("utf-8"))
        exit(1)
    else:
        logging.info('`ocm login` execution OK')
        logging.debug(ocm_login_stdout.strip().decode("utf-8"))

    logging.info('Attempting to log in OCM using `rosa login`')
    rosa_login_command = [rosa_cmnd, "login", "--token", args.ocm_token]
    if args.rosa_env:
        rosa_login_command.append("--env")
        rosa_login_command.append(args.rosa_env)
    logging.debug(rosa_login_command)
    rosa_login_process = subprocess.Popen(rosa_login_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    rosa_login_stdout, rosa_login_stderr = rosa_login_process.communicate()
    if rosa_login_process.returncode != 0:
        logging.error('%s unable to execute `rosa login`' % rosa_cmnd)
        logging.error(rosa_login_stderr.strip().decode("utf-8"))
        exit(1)
    else:
        logging.info('`rosa login` execution OK')
        logging.debug(rosa_login_stdout.strip().decode("utf-8"))

    if args.rosa_init:
        logging.info('Executing `rosa init` command to configure AWS account')
        rosa_init_command = [rosa_cmnd, "init", "--token", args.ocm_token]
        if args.rosa_env:
            rosa_login_command.append("--env")
            rosa_login_command.append(args.rosa_env)
        logging.debug(rosa_init_command)
        rosa_init_process = subprocess.Popen(rosa_init_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        rosa_init_stdout, rosa_init_stderr = rosa_init_process.communicate()
        if rosa_init_process.returncode != 0:
            logging.error('%s unable to execute `rosa init`' % rosa_cmnd)
            logging.error(rosa_init_stderr.strip().decode("utf-8"))
            exit(1)
        else:
            logging.info('`rosa init` execution OK')
            logging.debug(rosa_init_stdout.strip().decode("utf-8"))

    service_cluster = ""
    if args.provision_shard:
        service_cluster = _verify_provision_shard(ocm_cmnd, args.provision_shard)

    if args.oidc_config_id:
        oidc_config_id = args.oidc_config_id
        oidc_cleanup = False
        if not _verify_oidc_config_id(oidc_config_id, rosa_cmnd, my_path):
            logging.error('Provided oidc-config-id %s is not found in ROSA account. Exiting...' % oidc_config_id)
            exit(1)
    else:
        oidc_config_id = _gen_oidc_config_id(rosa_cmnd, cluster_name_seed, my_path)
        oidc_cleanup = True

    operator_roles_prefix = ""
    if args.common_operator_roles:
        installer_role_arn = _find_installer_role_arn(rosa_cmnd, my_path)
        roles_created = _gen_operator_roles(rosa_cmnd, cluster_name_seed, my_path, oidc_config_id, installer_role_arn)
        operator_roles_prefix = cluster_name_seed if roles_created else ""

    # Get connected to the Service Cluster
    logging.info("Getting information of %s Service Cluster" % service_cluster)
    sc_metadata = _get_mgmt_cluster_info(ocm_cmnd, service_cluster, es, args.es_index, args.es_index_retry, my_uuid) if service_cluster else None
    sc_kubeconfig_path = _download_kubeconfig(ocm_cmnd, sc_metadata['cluster_id'], my_path, "service") if service_cluster and 'cluster_id' in sc_metadata else ""
    access_to_service_cluster = True if sc_kubeconfig_path != "" else False

    if args.create_vpc:
        logging.info("Clusters Requested: %d. Clusters Per VPC: %d. VPCs to create: %d" % (args.cluster_count, args.clusters_per_vpc, math.ceil(args.cluster_count / args.clusters_per_vpc)))
        vpcs = _create_vpcs(terraform_cmnd, args.terraform_retry, my_path, cluster_name_seed, math.ceil(args.cluster_count / args.clusters_per_vpc), args.aws_region)
        if not vpcs:
            logging.error("Failed to create AWS VPCs, destroying them and exiting...")
            _destroy_vpcs(terraform_cmnd, args.terraform_retry, my_path, args.aws_region, vpcs)
            exit(1)

    # launch watcher thread to report status
    logging.info('Launching watcher thread')
    global force_terminate
    force_terminate = False
    all_clusters_installed = threading.Condition()
    watcher = threading.Thread(target=_watcher, args=(rosa_cmnd, my_path, cluster_name_seed, args.cluster_count, args.watcher_delay, my_uuid, all_clusters_installed, args.add_cluster_load))
    signal.signal(signal.SIGINT, set_force_terminate)
    watcher.daemon = True
    watcher.start()
    logging.info('Attempting to start %d clusters with %d batch size' % (args.cluster_count, args.batch_size))
    cluster_thread_list = []
    batch_count = 0
    loop_counter = 0
    try:
        while (loop_counter < args.cluster_count):
            if force_terminate:
                logging.warning("Not creating cluster %s after Capturing Ctrl-C" % (cluster_name_seed + "-" + str(loop_counter).zfill(4)))
                loop_counter += 1
            else:
                create_cluster = False
                if args.batch_size != 0:
                    if args.delay_between_batch is None:
                        # We add 2 to the batch size. 1 for the main thread and 1 for the watcher
                        while (args.batch_size + 2) <= threading.active_count():
                            # Wait for thread count to drop before creating another
                            time.sleep(1)
                        loop_counter += 1
                        create_cluster = True
                    elif batch_count >= args.batch_size:
                        time.sleep(args.delay_between_batch)
                        batch_count = 0
                    else:
                        batch_count += 1
                        loop_counter += 1
                        create_cluster = True
                else:
                    loop_counter += 1
                    create_cluster = True
                if create_cluster:
                    logging.debug('Starting Cluster thread %d' % (loop_counter + 1))
                    pattern = re.compile(r"^(\d+)(,\s*\d+)*$")
                    if args.workers.isdigit():
                        workers = int(args.workers)
                    else:
                        workers = int(args.workers.split(",")[(loop_counter - 1) % len(args.workers.split(","))])
                    if args.add_cluster_load:
                        low_jobs = max(0, int((args.cluster_load_jobs_per_worker * workers) - (float(args.cluster_load_job_variation) * float(args.cluster_load_jobs_per_worker * workers) / 100)))
                        high_jobs = int((args.cluster_load_jobs_per_worker * workers) + (float(args.cluster_load_job_variation) * float(args.cluster_load_jobs_per_worker * workers) / 100))
                        jobs = random.randint(low_jobs, high_jobs)
                        logging.debug("Selected jobs: %d" % jobs)
                    else:
                        jobs = 0
                    vpc_info = ""
                    if args.create_vpc:
                        vpc_info = vpcs[(loop_counter - 1) % len(vpcs)]
                        logging.debug("Creating cluster on VPC %s, with subnets: %s" % (vpc_info[0], vpc_info[1]))
                    try:
                        thread = threading.Thread(target=_build_cluster, args=(ocm_cmnd, rosa_cmnd, cluster_name_seed, args.must_gather_all, args.provision_shard, args.create_vpc, vpc_info, args.workers_wait_time, args.add_cluster_load, args.cluster_load_duration, jobs, workers, my_path, my_uuid, loop_counter, es, args.es_url, args.es_index, args.es_index_retry, service_cluster, sc_kubeconfig_path, all_clusters_installed, oidc_config_id, args.workload_type, args.kube_burner_version, args.e2e_git_details, args.git_branch, operator_roles_prefix))
                    except Exception as err:
                        logging.error(err)
                    cluster_thread_list.append(thread)
                    thread.start()
                    logging.debug('Number of alive threads %d' % threading.active_count())

    except Exception as err:
        logging.error(err)
        logging.error('Thread creation failed')

    logging.info('All clusters (%d) requested. Waiting for installations to finish' % len(cluster_thread_list))
    watcher.join()

    # Wait for active threads to finish
    logging.info('Waiting for  all threads (%d) to finish' % len(cluster_thread_list))
    for t in cluster_thread_list:
        try:
            t.join()
        except RuntimeError as err:
            if 'cannot join current thread' in err.args[0]:
                # catchs main thread
                continue
            else:
                raise

    if access_to_service_cluster:
        logging.info('Collect must-gather from Service Cluster %s' % sc_metadata['cluster_name'])
        _get_mgmt_cluster_must_gather(sc_kubeconfig_path, my_path)

    if args.cleanup_clusters:
        logging.info("Waiting %d minutes before starting the deleting process" % args.wait_before_cleanup)
        time.sleep(args.wait_before_cleanup * 60)
        logging.info('Attempting to delete all hosted clusters with seed %s' % (cluster_name_seed))
        delete_cluster_thread_list = []
        cmd = [rosa_cmnd, "list", "clusters", "-o", "json"]
        logging.debug(cmd)
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, preexec_fn=disable_signals)
        stdout, stderr = process.communicate()
        try:
            clusters = json.loads(stdout)
        except ValueError as err:
            logging.error("Failed to get clusters list: %s" % err)
            logging.error(stdout)
            logging.error(stderr)
            clusters = {}
        for cluster in clusters:
            if 'name' in cluster and cluster_name_seed in cluster['name']:
                logging.debug('Starting cluster cleanup %s' % cluster['name'])
                try:
                    thread = threading.Thread(target=_cleanup_cluster, args=(rosa_cmnd, cluster['name'], my_path, my_uuid, es, args.es_index, args.es_index_retry))
                except Exception as err:
                    logging.error('Thread creation failed')
                    logging.error(err)
                delete_cluster_thread_list.append(thread)
                thread.start()
                logging.debug('Number of alive threads %d' % threading.active_count())
                if args.delay_between_cleanup != 0:
                    logging.info('Waiting %d seconds for deleting the next cluster' % args.delay_between_cleanup)
                    time.sleep(args.delay_between_cleanup)
        # Wait for active threads to finish
        logging.info('All clusters (%d) requested to be deleted. Waiting for them to finish' % len(delete_cluster_thread_list))
        for t in delete_cluster_thread_list:
            try:
                t.join()
            except RuntimeError as err:
                if 'cannot join current thread' in err.args[0]:
                    # catchs main thread
                    continue
                else:
                    raise

        deleted_roles = _delete_operator_roles(rosa_cmnd, cluster_name_seed, my_path) if args.common_operator_roles else None

        if oidc_cleanup:
            delete_oidc_cmd = [rosa_cmnd, 'delete', 'oidc-config', '--oidc-config-id', oidc_config_id, '-m', 'auto', '-y']
            delete_oidc_process = subprocess.Popen(delete_oidc_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            delete_oidc_stdout, delete_oidc_stderr = delete_oidc_process.communicate()
            if delete_oidc_process.returncode != 0:
                logging.error('Deletion of OIDC ID %s failed. Manual Cleanup Required' % oidc_config_id)
                logging.error(delete_oidc_stderr.strip().decode("utf-8"))

        if args.create_vpc:
            # Hard code to a single destroy vpc retry so we do not endlessly retry when clusters fail to uninstall
            destroy_result = _destroy_vpcs(terraform_cmnd, 1, my_path, args.aws_region, vpcs)
            if destroy_result == 1:
                logging.error("Failed to destroy all VPCs, please manually delete them")

    if args.cleanup:
        logging.info('Cleaning working directory %s' % my_path)
        shutil.rmtree(my_path)

    stuck_clusters = [rosa_cmnd, 'list', 'clusters', '-o', 'json']
    stuck_process = subprocess.Popen(stuck_clusters, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stuck_stdout, stuck_stderr = stuck_process.communicate()

# Last, output test result
    logging.info('************************************************************************')
    logging.info('********* Summary for test %s *********' % (my_uuid))
    logging.info('************************************************************************')
    logging.info('Requested Clusters for test %s: %d' % (my_uuid, args.cluster_count))
    logging.info('Batches size: %s' % (str(args.batch_size)))
    logging.info('Delay between batches: %s' % (str(args.delay_between_batch)))
    logging.info('Cluster Name Seed: %s' % (cluster_name_seed))
    if stuck_process.returncode != 0:
        logging.error('Could not validate if any clusters were stuck')
    else:
        for cluster_exists in json.loads(stuck_stdout):
            if cluster_name_seed in cluster_exists["name"]:
                logging.info('%s still exists' % cluster_exists["name"])


if __name__ == '__main__':
    sys.exit(main())
