# Single table (architecture §9.3). PROVISIONED 5/5 with auto-scaling left off — the always-free
# tier applies to provisioned capacity ONLY; on-demand bills from the first write (PRD §8.2, R4).
resource "aws_dynamodb_table" "screener" {
  name         = var.dynamodb_table
  billing_mode = "PROVISIONED"
  hash_key     = "PK"
  range_key    = "SK"

  read_capacity  = 5
  write_capacity = 5

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}
