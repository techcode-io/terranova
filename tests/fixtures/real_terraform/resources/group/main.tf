/* @name Greeting
@tag e2e */
resource "terraform_data" "greeting" {
  input = templatefile("${path.module}/assets/greeting.tftpl", { name = "world" })
}

output "greeting" {
  value = trimspace(terraform_data.greeting.output)
}
