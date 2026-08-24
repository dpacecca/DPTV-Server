import { useState } from "react";
import { Button, Card, Center, Container, PasswordInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { useAuth } from "../auth/AuthContext";

export default function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(username, password);
    } catch {
      setError("Invalid username or password");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Center h="100vh">
      <Container size={400} w="100%">
        <Card withBorder shadow="sm" p="xl" radius="md">
          <form onSubmit={handleSubmit}>
            <Stack>
              <Title order={2} ta="center">
                DPTV-Server
              </Title>
              <Text c="dimmed" size="sm" ta="center">
                Sign in to manage your playlists
              </Text>
              <TextInput label="Username" value={username} onChange={(e) => setUsername(e.currentTarget.value)} required />
              <PasswordInput
                label="Password"
                value={password}
                onChange={(e) => setPassword(e.currentTarget.value)}
                required
              />
              {error && (
                <Text c="red" size="sm">
                  {error}
                </Text>
              )}
              <Button type="submit" loading={loading} fullWidth>
                Sign in
              </Button>
            </Stack>
          </form>
        </Card>
      </Container>
    </Center>
  );
}
