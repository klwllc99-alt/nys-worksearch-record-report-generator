def runCmd(String unixCmd, String windowsCmd = null) {
    if (isUnix()) {
        sh unixCmd
    } else {
        bat(windowsCmd ?: unixCmd)
    }
}

pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    environment {
        IMAGE_NAME = 'nys-worksearch-record-report-generator'
        CONTAINER_NAME = "nys-worksearch-record-generator-${env.BUILD_NUMBER}"
        CI_PORT = '8081'
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
                    runCmd(
                        "docker build -t ${IMAGE_NAME}:${BUILD_NUMBER} backend",
                        "docker build -t %IMAGE_NAME%:%BUILD_NUMBER% backend"
                    )
                }
            }
        }

        stage('Smoke test') {
            steps {
                script {
                    runCmd(
                        """
                        docker rm -f ${CONTAINER_NAME} >/dev/null 2>&1 || true
                        docker run -d --name ${CONTAINER_NAME} -p ${CI_PORT}:8080 \
                          -e ADMIN_TOKEN=local-support-token \
                          -e DEFAULT_ADMIN_EMAIL=klwllc99@gmail.com \
                          -e DEFAULT_ADMIN_PASSWORD=99klwllc \
                          -e METRICS_MAX_EVENTS=5000 \
                          -v \"$WORKSPACE/WS5.pdf:/app/static/ws5_blank.pdf:ro\" \
                          ${IMAGE_NAME}:${BUILD_NUMBER}
                        """.stripIndent(),
                        """
                        docker rm -f %CONTAINER_NAME% 1>nul 2>nul
                        docker run -d --name %CONTAINER_NAME% -p %CI_PORT%:8080 ^
                          -e ADMIN_TOKEN=local-support-token ^
                          -e DEFAULT_ADMIN_EMAIL=klwllc99@gmail.com ^
                          -e DEFAULT_ADMIN_PASSWORD=99klwllc ^
                          -e METRICS_MAX_EVENTS=5000 ^
                          -v "%WORKSPACE%\\WS5.pdf:/app/static/ws5_blank.pdf:ro" ^
                          %IMAGE_NAME%:%BUILD_NUMBER%
                        """.stripIndent()
                    )

                    runCmd(
                        """
                        for i in $(seq 1 30); do
                          curl -fsS http://127.0.0.1:${CI_PORT}/api/health && exit 0
                          sleep 2
                        done
                        exit 1
                        """.stripIndent(),
                        """
                        powershell -NoProfile -Command "$ok=$false; for($i=0; $i -lt 30; $i++){ try { $r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:%CI_PORT%/api/health; if($r.StatusCode -eq 200){ $ok=$true; break } } catch {}; Start-Sleep -Seconds 2 }; if(-not $ok){ exit 1 }"
                        """.stripIndent()
                    )

                    runCmd(
                        """
                        status=$(curl -s -o generated.pdf -w '%{http_code}' -F 'file=@backend/official_sample.csv' -F 'output_mode=single' http://127.0.0.1:${CI_PORT}/api/generate)
                        test "$status" = "200"
                        """.stripIndent(),
                        """
                        powershell -NoProfile -Command "$status = & curl.exe -s -o generated.pdf -w '%{http_code}' -F 'file=@backend/official_sample.csv' -F 'output_mode=single' http://127.0.0.1:%CI_PORT%/api/generate; if($status -ne '200'){ exit 1 }"
                        """.stripIndent()
                    )
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
                runCmd(
                    "docker rm -f ${CONTAINER_NAME} >/dev/null 2>&1 || true",
                    'docker rm -f %CONTAINER_NAME% 1>nul 2>nul'
                )
            }
        }
    }
}
