import inspect
import os
import sys
from functools import lru_cache
from typing import Dict, Union

import github
import yaml
from github import Auth, Github, Organization, Repository

import config
from src import create_logger, kube, kubeconfig, UnknownServiceaccountToken


@lru_cache()
def get_kubeconfig_for_serviceaccount(name: str, namespace: str) -> str:
    serviceaccount = kube.find_serviceaccount_token(name, namespace)
    if not serviceaccount:
        raise UnknownServiceaccountToken(f"couldn't find serviceaccount-token '{name}' in '{namespace}'")

    k8sconfig = kubeconfig.create(cluster_name=config.CLUSTER["name"], cluster_url=config.CLUSTER["url"],
                                  user_name=name, user_token=serviceaccount["token"], ca_data=serviceaccount["ca.crt"])

    return yaml.dump(k8sconfig)


def create_secret(github_entity: Union[Organization, Repository], entity_config: Dict):
    secret_value = get_kubeconfig_for_serviceaccount(entity_config["serviceaccount"]["name"],
                                                     entity_config["serviceaccount"]["namespace"])

    github_entity.create_secret(config.KUBECONFIG_SECRET_NAME, secret_value)


def update_github_secrets() -> bool:
    logger = create_logger(inspect.currentframe().f_code.co_name)

    success = True
    for organization in config.ORGANIZATIONS:
        # don't access GitHub api if not necessary
        if not organization.get("repos") and not organization.get("serviceaccount"):
            logger.debug(f"skip {organization} due to no defined/empty repos/serviceaccount key")
            continue

        access_token = os.getenv(organization["token_environment_variable_name"])
        if not access_token:
            logger.error(f"empty access_token for {organization}")
            success = False
            continue

        auth = Auth.Token(access_token)
        github_api = Github(auth=auth)

        try:
            github_organization = github_api.get_organization(organization["name"])
            if organization.get("serviceaccount"):
                logger.info(f"create secret for organization {organization}")
                create_secret(github_organization, organization)
        except github.GithubException:
            try:
                # fake it til you make it
                github_organization = github_api.get_user(organization["name"])
            except GithubException:
                logger.error(f"failed to retrieve github user for {organization}", exc_info=True)
                success = False
                continue
        except UnknownServiceaccountToken:
            logger.error(f"failed to retrieve serviceaccount token for {organization}", exc_info=True)
            success = False
            continue

        for repo in organization.get("repos", []):
            try:
                logger.info(f"create secret for repo {repo}")
                github_repo = github_organization.get_repo(repo["name"])
            except github.GithubException:
                logger.error(f"couldn't retrieve repo {repo}", exc_info=True)
                success = False
                continue

            try:
                create_secret(github_repo, repo)
            except github.GithubException:
                logger.error(f"failed to create repository secret for {repo}", exc_info=True)
                success = False
            except UnknownServiceaccountToken:
                logger.error(f"failed to retrieve serviceaccount token for {repo}", exc_info=True)
                success = False
                continue

    return success


if __name__ == "__main__":
    if not update_github_secrets():
        sys.exit(1)
