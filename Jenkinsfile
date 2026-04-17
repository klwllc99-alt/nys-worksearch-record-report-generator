def runCmd(String unixCmd, String windowsCmd = null) {
    if (isUnix()) {
        sh unixCmd
    } else {
        bat(windowsCmd ?: unixCmd)
    }
}

def buildContext() {
    def safeBranch = (env.BRANCH_NAME ?: 'local').replaceAll(/[^A-Za-z0-9_.-]/, '-')
    def imageTag = "${safeBranch}-${env.BUILD_NUMBER}"
    def containerName = "nys-worksearch-record-generator-${safeBranch}-${env.BUILD_NUMBER}"
    def ciPort = (8000 + Math.abs((env.JOB_NAME ?: safeBranch).hashCode() % 500)).toString()
    return [safeBranch: safeBranch, imageTag: imageTag, containerName: containerName, ciPort: ciPort]
}

pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    triggers {
        pollSCM('H/2 * * * *')
    }

    environment {
        IMAGE_NAME = 'nys-worksearch-record-report-generator'
        DEPLOY_WITH_COMPOSE = "${env.DEPLOY_WITH_COMPOSE ?: 'false'}"
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build Docker image') {
            steps {
                script {
                    def ctx = buildContext()
                    withEnv([
                        "SAFE_BRANCH=${ctx.safeBranch}",
                        "IMAGE_TAG=${ctx.imageTag}",
                        "CONTAINER_NAME=${ctx.containerName}",
                        "CI_PORT=${ctx.ciPort}",
                    ]) {
                        runCmd(
                            """
                            docker image rm -f ${env.IMAGE_NAME}:\$IMAGE_TAG >/dev/null 2>&1 || true
                            docker build --pull -t ${env.IMAGE_NAME}:\$IMAGE_TAG backend
                            """.stripIndent(),
                            """
                            docker image rm -f %IMAGE_NAME%:%IMAGE_TAG% 1>nul 2>nul
                            docker build --pull -t %IMAGE_NAME%:%IMAGE_TAG% backend
                            """.stripIndent()
                        )
                    }
                }
            }
        }

        stage('Smoke test') {
            steps {
                script {
                    def ctx = buildContext()
                    withEnv([
                        "SAFE_BRANCH=${ctx.safeBranch}",
                        "IMAGE_TAG=${ctx.imageTag}",
                        "CONTAINER_NAME=${ctx.containerName}",
                        "CI_PORT=${ctx.ciPort}",
                    ]) {
                        runCmd(
                            """
                            docker rm -f \$CONTAINER_NAME >/dev/null 2>&1 || true
                            docker run -d --name \$CONTAINER_NAME -p \$CI_PORT:8080 \
                              --label ci.branch=\$SAFE_BRANCH \
                              -e ADMIN_TOKEN=local-support-token \
                              -e DEFAULT_ADMIN_EMAIL=klwllc99@gmail.com \
                              -e DEFAULT_ADMIN_PASSWORD=99klwllc \
                              -e METRICS_MAX_EVENTS=5000 \
                              ${env.IMAGE_NAME}:\$IMAGE_TAG
                            """.stripIndent(),
                            """
                            docker rm -f %CONTAINER_NAME% 1>nul 2>nul
                            docker run -d --name %CONTAINER_NAME% -p %CI_PORT%:8080 ^
                              --label ci.branch=%SAFE_BRANCH% ^
                              -e ADMIN_TOKEN=local-support-token ^
                              -e DEFAULT_ADMIN_EMAIL=klwllc99@gmail.com ^
                              -e DEFAULT_ADMIN_PASSWORD=99klwllc ^
                              -e METRICS_MAX_EVENTS=5000 ^
                              %IMAGE_NAME%:%IMAGE_TAG%
                            """.stripIndent()
                        )

                        runCmd(
                            """
                            for i in \$(seq 1 30); do
                              docker exec \$CONTAINER_NAME python -c "import sys, urllib.request; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=5).status == 200 else sys.exit(1)" && exit 0
                              sleep 2
                            done
                            exit 1
                            """.stripIndent(),
                            """
                            powershell -NoProfile -Command "\$ok=\$false; for(\$i=0; \$i -lt 30; \$i++){ try { \$r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:%CI_PORT%/api/health; if(\$r.StatusCode -eq 200){ \$ok=\$true; break } } catch {}; Start-Sleep -Seconds 2 }; if(-not \$ok){ exit 1 }"
                            """.stripIndent()
                        )

                        runCmd(
                            """
                            docker exec \$CONTAINER_NAME python - <<'PY'
import sys
import uuid
import urllib.request

boundary = uuid.uuid4().hex
with open('/app/official_sample.csv', 'rb') as source:
    file_data = source.read()
parts = [
    f'--{boundary}\r\nContent-Disposition: form-data; name="output_mode"\r\n\r\nsingle\r\n'.encode(),
    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="official_sample.csv"\r\nContent-Type: text/csv\r\n\r\n'.encode() + file_data + b'\r\n',
    f'--{boundary}--\r\n'.encode(),
]
request = urllib.request.Request(
    'http://127.0.0.1:8080/api/generate',
    data=b''.join(parts),
    headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
)
with urllib.request.urlopen(request, timeout=60) as response:
    sys.exit(0 if response.status == 200 else 1)
PY
                            """.stripIndent(),
                            """
                            powershell -NoProfile -Command "\$status = & curl.exe -s -o generated.pdf -w '%{http_code}' -F 'file=@backend/official_sample.csv' -F 'output_mode=single' http://127.0.0.1:%CI_PORT%/api/generate; if(\$status -ne '200'){ exit 1 }"
                            """.stripIndent()
                        )
                    }
                }
            }
            post {
                success {
                    archiveArtifacts artifacts: 'generated.pdf', allowEmptyArchive: true, fingerprint: true
                }
            }
        }

        stage('Deploy with Docker Compose') {
            when {
                allOf {
                    branch 'main'
                    expression { env.DEPLOY_WITH_COMPOSE == 'true' }
                }
            }
            steps {
                script {
                    runCmd(
                        'docker compose up -d --build',
                        'docker compose up -d --build'
                    )
                }
            }
        }
    }

    post {
        always {
            script {
                def ctx = buildContext()
                withEnv([
                    "SAFE_BRANCH=${ctx.safeBranch}",
                    "IMAGE_TAG=${ctx.imageTag}",
                    "CONTAINER_NAME=${ctx.containerName}",
                    "CI_PORT=${ctx.ciPort}",
                ]) {
                    runCmd(
                        """
                        docker rm -f \$CONTAINER_NAME >/dev/null 2>&1 || true
                        docker image rm -f ${env.IMAGE_NAME}:\$IMAGE_TAG >/dev/null 2>&1 || true
                        """.stripIndent(),
                        """
                        docker rm -f %CONTAINER_NAME% 1>nul 2>nul
                        docker image rm -f %IMAGE_NAME%:%IMAGE_TAG% 1>nul 2>nul
                        """.stripIndent()
                    )
                }
            }
        }
    }
}
