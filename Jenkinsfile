
pipeline {
    agent any

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                echo 'GitHub source code downloaded successfully!'
            }
        }

        stage('Verify Project') {
            steps {
                bat 'echo === Project Files ==='
                bat 'dir'
                bat 'echo === Python Version ==='
                bat 'python --version'
            }
        }
    }
}
