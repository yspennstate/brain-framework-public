namespace MathBrain

theorem nat_add_zero (n : Nat) : n + 0 = n := by
  exact Nat.add_zero n

theorem nat_zero_add (n : Nat) : 0 + n = n := by
  exact Nat.zero_add n

theorem nat_add_comm_checked (a b : Nat) : a + b = b + a := by
  exact Nat.add_comm a b

#print axioms nat_add_zero
#print axioms nat_zero_add
#print axioms nat_add_comm_checked

end MathBrain
