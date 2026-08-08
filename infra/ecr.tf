# One repository for the single shared image (architecture A10). Image storage is the only
# non-zero cost in the system.
resource "aws_ecr_repository" "screener" {
  name                 = var.project
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Retaining ~2 images is REQUIRED, not optional — without it this line item grows without bound
# (architecture §9.2, R5).
resource "aws_ecr_lifecycle_policy" "screener" {
  repository = aws_ecr_repository.screener.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the 2 most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 2
        }
        action = { type = "expire" }
      }
    ]
  })
}
