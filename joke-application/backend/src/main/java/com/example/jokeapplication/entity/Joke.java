package com.example.jokeapplication.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

@Entity
@Table(name = "jokes")
@Getter
@Setter
@NoArgsConstructor
public class Joke {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, length = 500)
    private String setup;

    @Column(nullable = false, length = 500)
    private String punchline;
}
