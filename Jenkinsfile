node {
  def project = 'vkit-pipeline'
  def imageTag = "gcr.io/vkit-pipeline/artman-image-by-jenkins:${env.BRANCH_NAME}.${env.BUILD_NUMBER}"

  checkout scm

  stage 'Build image'
  sh("docker build -t ${imageTag} --no-cache .")

  stage 'Push image to registry'
  sh("gcloud docker push ${imageTag}")

  stage "Deploy Application"
  switch (env.BRANCH_NAME) {
    // Roll out to staging
    case "staging":
        // Change deployed image in staging to the one we just built
        sh("sed -i.bak 's#gcr.io/vkit-pipeline/artman-image-by-jenkins:placleholder#${imageTag}#' ./k8s/nightly.yaml")
        sh("kubectl --namespace=production apply -f k8s/nightly.yaml")
      //  sh("echo http://`kubectl --namespace=production get service/${feSvcName} --output=json | jq -r '.status.loadBalancer.ingress[0].ip'` > ${feSvcName}")
        break

    // Roll out to production
    case "master":
        echo 'Do nothing here'
        break

    // Roll out a dev environment
    default:
        sh("sed -i.bak 's#gcr.io/vkit-pipeline/artman-image-by-jenkins:placleholder#${imageTag}#' ./k8s/nightly.yaml")
        sh("kubectl --namespace=production apply -f k8s/nightly.yaml")
  }
}
