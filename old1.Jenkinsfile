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
                    python3 -m py_compile healthcheck.py ci/check_vrf_consistency.py

                    echo "── YAML lint (inventory) ──"
                    python3 -m yamllint -d "{extends: default, rules: {line-length: disable, document-start: disable}}" inventory/
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
                sh '''
                    . venv/bin/activate
                    ansible-playbook playbooks/render.yaml
                '''
            }
        }

        stage('Validate') {
            steps {
                sh '''
                    . venv/bin/activate
                    python3 ci/check_vrf_consistency.py
                '''
            }
        }

        stage('Deploy (main only)') {
            when {
                branch 'main'
            }
            steps {
                sh '''
                    . venv/bin/activate
                    python3 healthcheck.py
                    ansible-playbook playbooks/deploy.yaml
                    python3 healthcheck.py
                    ansible-playbook playbooks/save.yaml
                '''
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
        }
        success {
            echo "Build succeeded on branch ${env.BRANCH_NAME}"
        }
        failure {
            echo "Build failed on branch ${env.BRANCH_NAME} — check console output above."
        }
    }
}