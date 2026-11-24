
pipeline {
    agent any

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                echo 'GitHub source code downloaded successfully!'
            }
        }

        stage('Python Version') {
            steps {
                bat 'python --version'
                bat 'python -m pip --version'
            }
        }

        stage('Create Virtual Environment') {
            steps {
                bat 'python -m venv .venv'
            }
        }

        stage('Install Dependencies') {
            steps {
                bat '.venv\\Scripts\\python.exe -m pip install --upgrade pip'
                bat '.venv\\Scripts\\python.exe -m pip install -r requirements.txt'
            }
        }

        stage('Run Tests') {
            steps {
                bat '.venv\\Scripts\\python.exe -m pytest -q'
            }
        }
    }
}
