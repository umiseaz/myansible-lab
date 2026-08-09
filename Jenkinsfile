pipeline {
    agent any

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Quick Syntax Checks') {
            steps {
                sh '''
                    echo "── Python syntax check ──"
                    python3 -m py_compile verification/healthcheck.py verification/secrets_resolver.py verification/nornir_transform.py ci/check_vrf_consistency.py ci/check_data_consistency.py

                    echo "── YAML lint (inventory) ──"
                    python3 -m yamllint -d "{extends: default, rules: {line-length: disable, document-start: disable}}" inventory/ verification/nornir_config.yaml
                '''
            }
        }

        stage('Setup venv') {
            steps {
                sh '''
                    python3 -m venv venv
                    . venv/bin/activate
                    pip install --upgrade pip
                    pip install -r requirements.txt
                    ansible-galaxy collection install cisco.ios
                '''
            }
        }

        stage('Template Syntax Check') {
            steps {
                sh '''
                    . venv/bin/activate
                    python3 -c "
import sys
import os
from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError

env = Environment(loader=FileSystemLoader(\'templates/\'))
failed = False
for f in os.listdir(\'templates/\'):
    if f.endswith(\'.j2\'):
        try:
            env.get_template(f)
            print(f\'  [OK] {f}\')
        except TemplateSyntaxError as e:
            print(f\'  [FAIL] {f}: {e}\')
            failed = True
sys.exit(1 if failed else 0)
"
                '''
            }
        }

        stage('Render Configs') {
            steps {
                withCredentials([
                    string(credentialsId: 'ansible-vault-password', variable: 'ANSIBLE_VAULT_PASSWORD')
                ]) {
                    sh '''
                        . venv/bin/activate
                        echo "$ANSIBLE_VAULT_PASSWORD" > .vault_pass
                        ansible-playbook playbooks/render.yaml
                    '''
                }
            }
        }

        stage('Validate') {
            steps {
                sh '''
                    . venv/bin/activate
                    python3 ci/check_vrf_consistency.py
                    python3 ci/check_data_consistency.py
                '''
            }
        }

        stage('Deploy (main only)') {
            when {
                branch 'main'
            }
            steps {
                withCredentials([
                    string(credentialsId: 'ansible-vault-password', variable: 'ANSIBLE_VAULT_PASSWORD'),
                    usernamePassword(credentialsId: 'lab-router-admin-creds', usernameVariable: 'NORNIR_USERNAME', passwordVariable: 'NORNIR_PASSWORD'),
                    string(credentialsId: 'lab-router-enable-secret', variable: 'NORNIR_ENABLE_SECRET'),
                    string(credentialsId: 'lab-ospf-auth-key', variable: 'OSPF_AUTH_KEY')
                ]) {
                    sh '''
                        . venv/bin/activate
                        echo "$ANSIBLE_VAULT_PASSWORD" > .vault_pass
                        python3 verification/healthcheck.py
                        ansible-playbook playbooks/deploy.yaml
                        python3 verification/healthcheck.py
                        ansible-playbook playbooks/save.yaml
                    '''
                }
            }
        }

        stage('Tag last successful deploy') {
            when {
                branch 'main'
            }
            steps {
                withCredentials([usernamePassword(credentialsId: 'github-pat-admin-full-right', usernameVariable: 'GIT_USER', passwordVariable: 'GIT_TOKEN')]) {
                    sh '''
                        git config user.email "jenkins@nuc.local"
                        git config user.name "jenkins-ci"

                        git tag -f last_deploy_tag

                        git push "https://${GIT_USER}:${GIT_TOKEN}@github.com/umiseaz/myansible-lab.git" :refs/tags/last_deploy_tag || true
                        git push "https://${GIT_USER}:${GIT_TOKEN}@github.com/umiseaz/myansible-lab.git" last_deploy_tag
                    '''
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'rendered/*.cfg', fingerprint: true, allowEmptyArchive: true
            sh 'rm -f .vault_pass'
        }
        success {
            echo "Build succeeded on branch ${env.BRANCH_NAME}"
        }
        failure {
            echo "Build failed on branch ${env.BRANCH_NAME} — check console output above."
        }
    }
}
